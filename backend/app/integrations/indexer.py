"""
Document ingestion helpers for Azure AI Search.

Handles:
- Generating embeddings via Azure OpenAI (with batching and retry)
- Uploading document batches to Azure AI Search
- Deduplication by document ID (SHA-256 of filepath + content hash)

Used by the indexing scripts (scripts/index_code.py, scripts/index_docs.py).
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import structlog
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient as AzureSearchClient
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

_UPLOAD_BATCH_SIZE = 100    # Azure AI Search max batch is 1000; keep well under
_EMBED_BATCH_SIZE = 16      # Azure OpenAI embedding batching


def _doc_id(filepath: str, content: str) -> str:
    """Stable document ID: base64url-safe hash of filepath + content."""
    raw = hashlib.sha256(f"{filepath}:{content}".encode()).hexdigest()
    # Azure Search IDs must be URL-safe — use first 40 hex chars
    return raw[:40]


def _embed_retry() -> AsyncRetrying:
    from openai import RateLimitError, APIConnectionError, InternalServerError
    return AsyncRetrying(
        retry=retry_if_exception_type((RateLimitError, APIConnectionError, InternalServerError)),
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=1, max=60, jitter=3),
        reraise=True,
    )


class Indexer:
    """
    Batched document uploader with embedding generation.

    Usage:
        async with Indexer(index_name="code-index") as idx:
            await idx.upload(documents)
    """

    def __init__(self, index_name: str) -> None:
        self._index_name = index_name
        self._credential = AzureKeyCredential(settings.azure_search_api_key)
        self._endpoint = settings.azure_search_endpoint
        self._embedding_deployment = settings.azure_openai_embedding_deployment
        self._openai: AsyncAzureOpenAI | AsyncOpenAI | None = None
        self._search: AzureSearchClient | None = None

    async def __aenter__(self) -> "Indexer":
        if self._embedding_deployment:
            if settings.llm_provider == "openai":
                self._openai = AsyncOpenAI(api_key=settings.openai_api_key)
            else:
                self._openai = AsyncAzureOpenAI(
                    azure_endpoint=settings.azure_openai_endpoint,
                    api_key=settings.azure_openai_api_key,
                    api_version=settings.azure_openai_api_version,
                )
        self._search = AzureSearchClient(
            endpoint=self._endpoint,
            index_name=self._index_name,
            credential=self._credential,
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._openai:
            await self._openai.close()
        if self._search:
            await self._search.close()

    async def upload(self, documents: list[dict[str, Any]]) -> int:
        """
        Embed and upload documents in batches.
        Returns the total number of documents successfully uploaded.
        """
        if not documents:
            return 0

        # Assign stable IDs
        for doc in documents:
            doc.setdefault("id", _doc_id(doc.get("filepath", ""), doc.get("content", "")))

        # Embed in batches
        if self._openai and self._embedding_deployment:
            await self._embed_all(documents)

        # Upload in batches
        total = 0
        for i in range(0, len(documents), _UPLOAD_BATCH_SIZE):
            batch = documents[i : i + _UPLOAD_BATCH_SIZE]
            result = await self._search.upload_documents(documents=batch)
            succeeded = sum(1 for r in result if r.succeeded)
            failed = len(batch) - succeeded
            if failed:
                logger.warning("Some documents failed to upload", failed=failed, batch_start=i)
            total += succeeded
            logger.info("Uploaded batch", start=i, end=i + len(batch), succeeded=succeeded)

        return total

    async def _embed_all(self, documents: list[dict]) -> None:
        """Embed all documents' content fields in batches."""
        for i in range(0, len(documents), _EMBED_BATCH_SIZE):
            batch = documents[i : i + _EMBED_BATCH_SIZE]
            texts = [doc.get("content", "")[:8000] for doc in batch]
            vectors = await self._embed_batch(texts)
            for doc, vec in zip(batch, vectors):
                if vec:
                    doc["content_vector"] = vec
            if i % (5 * _EMBED_BATCH_SIZE) == 0:
                logger.info("Embedding progress", done=i, total=len(documents))

    async def _embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        """Embed a list of texts, returning a vector per text (None on failure)."""
        try:
            async for attempt in _embed_retry():
                with attempt:
                    resp = await self._openai.embeddings.create(
                        input=texts,
                        model=self._embedding_deployment,
                    )
            return [item.embedding for item in resp.data]
        except Exception as exc:
            logger.error("Embedding batch failed", error=str(exc))
            return [None] * len(texts)
