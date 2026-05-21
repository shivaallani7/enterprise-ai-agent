"""
CodeContextPlugin — SK plugin that searches the code index in Azure AI Search.

Uses Annotated parameter descriptions so the LLM understands exactly what
each argument controls and writes better queries.
"""
from __future__ import annotations

from typing import Annotated

import structlog
from semantic_kernel.functions import kernel_function

from app.integrations.search_client import SearchClient

logger = structlog.get_logger()


class CodeContextPlugin:
    """Semantic Kernel plugin for code search and retrieval."""

    def __init__(self) -> None:
        self._search = SearchClient(index_type="code")

    @kernel_function(
        name="search_code",
        description=(
            "Semantic search over the codebase index. Use this to find relevant "
            "functions, classes, API handlers, or implementation patterns. "
            "Returns file path, line range, and code snippet for top results. "
            "Prefer specific queries over broad ones for better results."
        ),
    )
    async def search_code(
        self,
        query: Annotated[
            str,
            (
                "A natural-language or keyword query describing the code to find. "
                "Examples: 'JWT authentication middleware', 'Cosmos DB upsert session', "
                "'Jira story fetch by key'."
            ),
        ],
        top_k: Annotated[
            int,
            "Number of results to return. Use 3 for targeted lookups, 5-8 for broad exploration.",
        ] = 5,
    ) -> str:
        try:
            results = await self._search.search(query, top_k=top_k)
            if not results:
                return "No relevant code found."
            parts = []
            for r in results:
                lang = r.get("language", "")
                line_info = (
                    f"lines {r['line_start']}-{r['line_end']}"
                    if r.get("line_start") and r.get("line_end")
                    else ""
                )
                meta = " | ".join(filter(None, [line_info, r.get("chunk_type", "")]))
                parts.append(
                    f"File: {r['filepath']}"
                    + (f" ({meta})" if meta else "")
                    + f" [confidence: {r.get('confidence', 0):.0%}]\n"
                    f"```{lang}\n{r['content']}\n```"
                )
            return "\n\n---\n\n".join(parts)
        except Exception as exc:
            logger.warning("search_code failed", query=query, error=str(exc))
            return f"Code search failed: {exc}"

    @kernel_function(
        name="get_file",
        description=(
            "Retrieve the full content of a specific file by its path. "
            "Use this only when you already know the exact file path from "
            "a previous search_code result or from the story context."
        ),
    )
    async def get_file(
        self,
        filepath: Annotated[
            str,
            (
                "The exact repository-relative file path, e.g. "
                "'src/auth/middleware.py' or 'backend/app/api/chat.py'."
            ),
        ],
    ) -> str:
        try:
            result = await self._search.get_document(filepath)
            if not result:
                return f"File not found in index: {filepath}"
            return f"File: {filepath}\n\n```\n{result['content']}\n```"
        except Exception as exc:
            logger.warning("get_file failed", filepath=filepath, error=str(exc))
            return f"Could not retrieve file {filepath}: {exc}"
