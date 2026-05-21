"""Langchain tools wrapping the docs Azure AI Search index."""
from __future__ import annotations

from langchain_core.tools import tool
from app.integrations.search_client import SearchClient

_search = SearchClient(index_type="docs")


@tool
async def search_docs(query: str, top_k: int = 5) -> str:
    """Semantic search over documentation, architecture decision records, READMEs,
    runbooks, and wiki pages. Use for process, architecture, or project-level questions.
    Do NOT use for source code — use search_code for that."""
    try:
        results = await _search.search(query, top_k=top_k)
        if not results:
            return "No relevant documentation found."
        parts = []
        for r in results:
            title = r.get("title") or r.get("filepath") or "unknown"
            parts.append(f"Source: {title} [confidence: {r.get('confidence', 0):.0%}]\n\n{r['content']}")
        return "\n\n---\n\n".join(parts)
    except Exception as exc:
        return f"Documentation search failed: {exc}"


@tool
async def get_project_context() -> str:
    """Retrieve high-level project context: README, architecture overview, tech stack,
    and key conventions. Call at the start of broad project-level questions."""
    try:
        results = await _search.search(
            "project overview architecture README tech stack conventions", top_k=3
        )
        if not results:
            return "No project context documents found in the index."
        return "\n\n---\n\n".join(r["content"] for r in results)
    except Exception as exc:
        return f"Could not load project context: {exc}"


RAG_TOOLS = [search_docs, get_project_context]
