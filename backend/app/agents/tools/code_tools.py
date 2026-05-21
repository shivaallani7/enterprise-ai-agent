"""Langchain tools wrapping the code Azure AI Search index."""
from __future__ import annotations

from langchain_core.tools import tool
from app.integrations.search_client import SearchClient

_search = SearchClient(index_type="code")


@tool
async def search_code(query: str, top_k: int = 5) -> str:
    """Semantic search over the indexed codebase. Returns file path, line range,
    and code snippets. Use for finding implementations, functions, classes, or patterns.
    Use top_k=3 for targeted lookups, 5-8 for broader exploration."""
    try:
        results = await _search.search(query, top_k=top_k)
        if not results:
            return "No relevant code found."
        parts = []
        for r in results:
            lang = r.get("language", "")
            lines = f"lines {r['line_start']}-{r['line_end']}" if r.get("line_start") else ""
            meta = " | ".join(filter(None, [lines, r.get("chunk_type", "")]))
            parts.append(
                f"File: {r['filepath']}" + (f" ({meta})" if meta else "")
                + f" [confidence: {r.get('confidence', 0):.0%}]\n"
                f"```{lang}\n{r['content']}\n```"
            )
        return "\n\n---\n\n".join(parts)
    except Exception as exc:
        return f"Code search failed: {exc}"


@tool
async def get_file(filepath: str) -> str:
    """Retrieve the full content of a specific file by its exact repo-relative path.
    Only use when you already know the path from a previous search result."""
    try:
        result = await _search.get_document(filepath)
        if not result:
            return f"File not found in index: {filepath}"
        return f"File: {filepath}\n\n```\n{result['content']}\n```"
    except Exception as exc:
        return f"Could not retrieve {filepath}: {exc}"


@tool
async def list_files(path_prefix: str = "", top_k: int = 50) -> str:
    """List all indexed files whose path starts with the given prefix.
    Use path_prefix="" to list all files, "frontend/" to list frontend files, etc.
    Useful for answering questions about repo structure, available modules, or what
    files exist in a directory."""
    try:
        from azure.search.documents.aio import SearchClient as AzureSearchClient
        from azure.core.credentials import AzureKeyCredential
        from app.config import get_settings
        settings = get_settings()

        async with AzureSearchClient(
            endpoint=settings.azure_search_endpoint,
            index_name=settings.azure_search_code_index,
            credential=AzureKeyCredential(settings.azure_search_api_key),
        ) as client:
            # Use OData filter if prefix given, otherwise fetch all
            escaped = path_prefix.replace("'", "''")
            filter_expr = f"startswith(filepath, '{escaped}')" if path_prefix else None
            raw = await client.search(
                search_text="*",
                filter=filter_expr,
                top=top_k,
                select=["filepath", "language", "chunk_type"],
            )
            seen: set[str] = set()
            paths: list[str] = []
            async for result in raw:
                fp = result.get("filepath", "")
                if fp and fp not in seen:
                    seen.add(fp)
                    paths.append(fp)

        if not paths:
            return f"No indexed files found with prefix '{path_prefix}'."
        paths.sort()
        return f"Indexed files ({len(paths)}):\n" + "\n".join(paths)
    except Exception as exc:
        return f"list_files failed: {exc}"


CODE_TOOLS = [search_code, get_file, list_files]
