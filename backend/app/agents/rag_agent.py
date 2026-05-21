"""
RAGPlugin — SK plugin that queries the docs/wiki index in Azure AI Search.

Uses Annotated parameter descriptions so the LLM writes better doc queries.
"""
from __future__ import annotations

from typing import Annotated

import structlog
from semantic_kernel.functions import kernel_function

from app.integrations.search_client import SearchClient

logger = structlog.get_logger()


class RAGPlugin:
    """Semantic Kernel plugin for documentation and wiki retrieval."""

    def __init__(self) -> None:
        self._search = SearchClient(index_type="docs")

    @kernel_function(
        name="search_docs",
        description=(
            "Semantic search over documentation, architecture decision records, "
            "README files, runbooks, and wiki pages. Use this to answer questions "
            "about processes, architecture decisions, system design, or project conventions. "
            "Do not use for code — use CodeContext.search_code for that."
        ),
    )
    async def search_docs(
        self,
        query: Annotated[
            str,
            (
                "A natural-language query describing the documentation to find. "
                "Examples: 'authentication flow', 'deployment runbook', "
                "'database schema design decisions', 'onboarding steps'."
            ),
        ],
        top_k: Annotated[
            int,
            "Number of documents to return (1-10). Use 3-5 for most queries.",
        ] = 5,
    ) -> str:
        try:
            results = await self._search.search(query, top_k=top_k)
            if not results:
                return "No relevant documentation found."
            parts = []
            for r in results:
                title = r.get("title") or r.get("filepath") or "unknown"
                parts.append(
                    f"Source: {title} [confidence: {r.get('confidence', 0):.0%}]\n\n{r['content']}"
                )
            return "\n\n---\n\n".join(parts)
        except Exception as exc:
            logger.warning("search_docs failed", query=query, error=str(exc))
            return f"Documentation search failed: {exc}"

    @kernel_function(
        name="get_project_context",
        description=(
            "Retrieve high-level project context: README summary, architecture overview, "
            "tech stack, and key conventions. Call this at the start of a General tab "
            "conversation before answering broad questions about the project."
        ),
    )
    async def get_project_context(self) -> str:
        try:
            results = await self._search.search(
                "project overview architecture README tech stack conventions",
                top_k=3,
            )
            if not results:
                return "No project context documents found in the index."
            parts = [r["content"] for r in results]
            return "\n\n---\n\n".join(parts)
        except Exception as exc:
            logger.warning("get_project_context failed", error=str(exc))
            return f"Could not load project context: {exc}"
