"""
Azure AI Search client — hybrid search (BM25 + vector) with semantic reranking.

Search strategy
───────────────
Each query runs three retrieval modes fused by Reciprocal Rank Fusion (RRF):
  1. BM25 keyword search  — exact term matching, good for identifiers/filenames
  2. Vector similarity    — semantic matching via Azure OpenAI embeddings
  3. Semantic reranking   — cross-encoder reranker re-scores top-N candidates

This hybrid approach consistently outperforms any single mode for engineering
Q&A: "how does auth work" (semantic wins), "find JiraClient.get_story"
(keyword wins), "authentication middleware pattern" (both win together).

Vector search is skipped if AZURE_OPENAI_EMBEDDING_DEPLOYMENT is empty so
the client degrades gracefully in environments without an embedding deployment.

Score semantics
───────────────
Azure returns two scores per result:
  @search.score         — BM25/RRF score (keyword + vector fusion)
  @search.reranker_score — semantic reranker score, range 0-4 (higher = better)

We expose `reranker_score` when available, falling back to `score`.
The `confidence` field normalises whichever score is available to [0, 1].

OData filter safety
───────────────────
Single quotes in OData filter values must be escaped as ''.
`_odata_escape()` handles this so filepath lookups are injection-safe.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

import structlog
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient as AzureSearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    SearchableField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from openai import AsyncAzureOpenAI, AsyncOpenAI
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()

# ── Constants ──────────────────────────────────────────────────────────────────

_SEMANTIC_CONFIG = "default"
_VECTOR_FIELD = "content_vector"
_CODE_SELECT_FIELDS = ["id", "filepath", "content", "line_start", "line_end", "language", "chunk_type"]
_DOCS_SELECT_FIELDS = ["id", "filepath", "title", "content", "chunk_type"]


# ── Retry ──────────────────────────────────────────────────────────────────────

def _search_retry() -> AsyncRetrying:
    from azure.core.exceptions import ServiceRequestError, HttpResponseError
    return AsyncRetrying(
        retry=retry_if_exception_type((ServiceRequestError, HttpResponseError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=10, jitter=1),
        reraise=True,
    )


# ── Embedding helper ───────────────────────────────────────────────────────────

class _EmbeddingClient:
    """
    Thin wrapper around Azure OpenAI embeddings.
    Returns None if embedding deployment is not configured so the search
    client can fall back to keyword-only mode.
    """

    def __init__(self) -> None:
        self._deployment = settings.azure_openai_embedding_deployment
        self._client: AsyncAzureOpenAI | AsyncOpenAI | None = None
        if self._deployment:
            if settings.llm_provider == "openai":
                self._client = AsyncOpenAI(api_key=settings.openai_api_key)
            else:
                self._client = AsyncAzureOpenAI(
                    azure_endpoint=settings.azure_openai_endpoint,
                    api_key=settings.azure_openai_api_key,
                    api_version=settings.azure_openai_api_version,
                )

    async def embed(self, text: str) -> list[float] | None:
        if not self._client or not self._deployment:
            return None
        try:
            # Truncate to ~8000 chars to stay within embedding token limits
            resp = await self._client.embeddings.create(
                input=text[:8000],
                model=self._deployment,
            )
            return resp.data[0].embedding
        except Exception as exc:
            logger.warning("Embedding failed — falling back to keyword search", error=str(exc))
            return None


# Module-level singleton so we don't create a new OpenAI client per search call
_embedding_client = _EmbeddingClient()


# ── OData helpers ──────────────────────────────────────────────────────────────

def _odata_escape(value: str) -> str:
    """Escape single quotes in OData filter string values."""
    return value.replace("'", "''")


def _normalise_score(score: float | None, reranker: float | None) -> float:
    """
    Normalise to [0, 1].
    Reranker score is 0-4; BM25/RRF score has no fixed upper bound.
    """
    if reranker is not None:
        return round(min(reranker / 4.0, 1.0), 4)
    if score is not None:
        # BM25 scores are typically 0-20 for well-matching docs
        return round(min(score / 20.0, 1.0), 4)
    return 0.0


# ── Main client ────────────────────────────────────────────────────────────────

class SearchClient:
    """
    Hybrid Azure AI Search client for code-index and docs-index.

    Usage:
        client = SearchClient(index_type="code")
        results = await client.search("JWT middleware authentication")
    """

    def __init__(self, index_type: Literal["code", "docs"]) -> None:
        self._index_type = index_type
        self._index_name = (
            settings.azure_search_code_index
            if index_type == "code"
            else settings.azure_search_docs_index
        )
        self._credential = AzureKeyCredential(settings.azure_search_api_key)
        self._endpoint = settings.azure_search_endpoint
        self._select = (
            _CODE_SELECT_FIELDS if index_type == "code" else _DOCS_SELECT_FIELDS
        )

    def _get_client(self) -> AzureSearchClient:
        return AzureSearchClient(
            endpoint=self._endpoint,
            index_name=self._index_name,
            credential=self._credential,
        )

    async def search(self, query: str, top_k: int | None = None) -> list[dict]:
        """
        Hybrid search: BM25 + optional vector + semantic reranking.
        Returns results sorted by reranker_score descending.
        """
        k = top_k or settings.azure_search_top_k

        # Get embedding for vector leg (may be None if not configured)
        vector = await _embedding_client.embed(query)

        vector_queries: list[VectorizedQuery] = []
        if vector:
            vector_queries.append(
                VectorizedQuery(
                    vector=vector,
                    k_nearest_neighbors=k * 2,   # over-fetch; reranker trims
                    fields=_VECTOR_FIELD,
                    exhaustive=False,             # HNSW approximate search
                )
            )

        async for attempt in _search_retry():
            with attempt:
                async with self._get_client() as client:
                    raw = await client.search(
                        search_text=query,
                        vector_queries=vector_queries or None,
                        query_type="semantic",
                        semantic_configuration_name=_SEMANTIC_CONFIG,
                        top=k,
                        select=self._select,
                        query_caption="extractive",
                        query_answer="extractive",
                    )
                    docs: list[dict] = []
                    async for result in raw:
                        docs.append(self._normalise_result(result))

        # Sort by best available score descending
        docs.sort(key=lambda d: d["confidence"], reverse=True)
        return docs[:k]

    async def get_document(self, filepath: str) -> dict | None:
        """
        Retrieve a specific document by exact filepath match.
        filepath is OData-escaped to prevent filter injection.
        """
        escaped = _odata_escape(filepath)
        async for attempt in _search_retry():
            with attempt:
                async with self._get_client() as client:
                    raw = await client.search(
                        search_text="*",
                        filter=f"filepath eq '{escaped}'",
                        top=1,
                        select=["id", "filepath", "content", "language"],
                    )
                    async for result in raw:
                        return {
                            "filepath": result.get("filepath", ""),
                            "content": result.get("content", ""),
                            "language": result.get("language", ""),
                        }
        return None

    async def get_documents_for_story(self, story_key: str, top_k: int = 8) -> list[dict]:
        """
        Search for code related to a Jira story key.
        Useful for pre-populating story tab context beyond what the story fields contain.
        """
        return await self.search(story_key, top_k=top_k)

    def _normalise_result(self, result: Any) -> dict:
        """Convert an Azure SearchResult to a plain dict with normalised fields."""
        # Azure SDK uses __getitem__ for field access and special "@search.*" keys
        score: float | None = result.get("@search.score")
        reranker: float | None = result.get("@search.reranker_score")

        # Extractive caption — if available, prefer it as a summary snippet
        captions = result.get("@search.captions") or []
        caption_text = captions[0].text if captions else ""

        content = result.get("content", "")
        # Use caption as snippet if it's shorter and available
        display_content = caption_text if caption_text and len(caption_text) < len(content) else content

        return {
            "id": result.get("id", ""),
            "filepath": result.get("filepath", result.get("title", "")),
            "title": result.get("title", ""),
            "content": display_content,
            "full_content": content,
            "line_start": result.get("line_start"),
            "line_end": result.get("line_end"),
            "language": result.get("language", ""),
            "chunk_type": result.get("chunk_type", ""),
            "score": score,
            "reranker_score": reranker,
            "confidence": _normalise_score(score, reranker),
        }


# ── Index management ───────────────────────────────────────────────────────────

class IndexManager:
    """
    Creates and updates the code-index and docs-index schemas in Azure AI Search.

    Run once at initial deployment, and again whenever the schema changes.
    Safe to re-run — uses create-or-update semantics.
    """

    def __init__(self) -> None:
        self._credential = AzureKeyCredential(settings.azure_search_api_key)
        self._endpoint = settings.azure_search_endpoint
        self._dims = settings.azure_search_vector_dimensions

    def _get_index_client(self) -> SearchIndexClient:
        return SearchIndexClient(
            endpoint=self._endpoint,
            credential=self._credential,
        )

    def _vector_field(self) -> SearchField:
        return SearchField(
            name=_VECTOR_FIELD,
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=self._dims,
            vector_search_profile_name="hnsw-profile",
        )

    def _vector_search_config(self) -> VectorSearch:
        return VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw-algo")],
            profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw-algo")],
        )

    def _semantic_search_config(self, content_field: str, title_field: str | None = None) -> SemanticSearch:
        prioritized = SemanticPrioritizedFields(
            content_fields=[SemanticField(field_name=content_field)],
        )
        if title_field:
            prioritized.title_field = SemanticField(field_name=title_field)
        return SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name=_SEMANTIC_CONFIG,
                    prioritized_fields=prioritized,
                )
            ]
        )

    def build_code_index(self) -> SearchIndex:
        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
            SearchableField(name="filepath", type=SearchFieldDataType.String, filterable=True, sortable=True),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SimpleField(name="language", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="chunk_type", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="line_start", type=SearchFieldDataType.Int32, filterable=True, sortable=True),
            SimpleField(name="line_end", type=SearchFieldDataType.Int32, filterable=True),
            SimpleField(name="repo", type=SearchFieldDataType.String, filterable=True),
            self._vector_field(),
        ]
        return SearchIndex(
            name=settings.azure_search_code_index,
            fields=fields,
            vector_search=self._vector_search_config(),
            semantic_search=self._semantic_search_config(content_field="content"),
        )

    def build_docs_index(self) -> SearchIndex:
        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
            SearchableField(name="filepath", type=SearchFieldDataType.String, filterable=True, sortable=True),
            SearchableField(name="title", type=SearchFieldDataType.String),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SimpleField(name="chunk_type", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="source", type=SearchFieldDataType.String, filterable=True),
            # Extended RAG ingestion metadata
            SimpleField(name="doc_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="doc_type", type=SearchFieldDataType.String, filterable=True),
            SearchableField(name="file_name", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="file_hash", type=SearchFieldDataType.String),
            SearchableField(name="section_title", type=SearchFieldDataType.String),
            SimpleField(name="page_number", type=SearchFieldDataType.Int32, filterable=True),
            SimpleField(name="sheet_name", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="language", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="extraction_method", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="chunk_index", type=SearchFieldDataType.Int32, filterable=True),
            SimpleField(name="total_chunks", type=SearchFieldDataType.Int32),
            SimpleField(name="chunk_strategy", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="version", type=SearchFieldDataType.Int32, filterable=True),
            self._vector_field(),
        ]
        return SearchIndex(
            name=settings.azure_search_docs_index,
            fields=fields,
            vector_search=self._vector_search_config(),
            semantic_search=self._semantic_search_config(
                content_field="content",
                title_field="title",
            ),
        )

    async def create_or_update_indexes(self) -> None:
        async with self._get_index_client() as client:
            for index in (self.build_code_index(), self.build_docs_index()):
                result = await client.create_or_update_index(index)
                logger.info("Index created/updated", name=result.name, fields=len(result.fields))
