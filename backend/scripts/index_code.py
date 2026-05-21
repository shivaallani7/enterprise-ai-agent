"""
Index a local repository's source code into the Azure AI Search code-index.

Usage
─────
    # From the backend/ directory:
    python -m scripts.index_code --repo /path/to/your/repo

    # Dry run — print chunks without uploading:
    python -m scripts.index_code --repo /path/to/your/repo --dry-run

    # Limit to specific extensions:
    python -m scripts.index_code --repo /path/to/your/repo --ext .py .ts .tsx

Chunking strategy
─────────────────
Python files: AST-based chunking — each top-level function and class body
becomes one chunk so the LLM sees coherent, semantically complete units.
Functions / classes > MAX_CHARS are split at the midpoint.

All other files: sliding-window line chunking.
  - Chunk size: 80 lines
  - Overlap: 10 lines (so context at chunk boundaries is preserved)

Ignored paths: .git, node_modules, __pycache__, dist, build, .venv,
migrations, *.min.js, *.lock, *.map
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import os
import sys
from pathlib import Path

# Allow running as `python -m scripts.index_code` from the backend/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog

from app.config import get_settings
from app.integrations.indexer import Indexer
from app.integrations.search_client import IndexManager

logger = structlog.get_logger()
settings = get_settings()

# ── Configuration ──────────────────────────────────────────────────────────────

DEFAULT_EXTENSIONS = {
    # Source code
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go", ".cs", ".rs",
    # Project structure & documentation (answer "what is the structure / how does X work")
    ".md", ".mdx", ".txt", ".rst",
    # Config & manifest (answer "what dependencies / how is it configured")
    ".json", ".yaml", ".yml", ".toml", ".env.example",
}
IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", "dist", "build", ".venv", "venv",
    "migrations", ".mypy_cache", ".pytest_cache", "coverage", ".next",
}
IGNORED_SUFFIXES = {".min.js", ".map", ".lock", ".ico", ".png", ".jpg", ".svg"}
MAX_CHARS = 3_000      # max characters per chunk (≈750 tokens)
LINE_CHUNK = 80        # lines per chunk for non-Python files
LINE_OVERLAP = 10      # line overlap between chunks

LANGUAGE_MAP = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".java": "java",
    ".go": "go", ".cs": "csharp", ".rs": "rust",
    ".md": "markdown", ".mdx": "markdown", ".rst": "rst", ".txt": "text",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
}


# ── Python AST chunking ────────────────────────────────────────────────────────

def _python_chunks(filepath: str, source: str) -> list[dict]:
    """
    Chunk a Python file by top-level function and class definitions.
    Falls back to line chunking if the file fails to parse.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return _line_chunks(filepath, source, language="python")

    lines = source.splitlines()
    chunks = []

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue

        start = node.lineno - 1       # 0-indexed
        end = node.end_lineno         # exclusive
        body = "\n".join(lines[start:end])

        chunk_type = "class" if isinstance(node, ast.ClassDef) else "function"

        if len(body) > MAX_CHARS:
            # Split long nodes at the midpoint
            mid = (start + end) // 2
            for seg_start, seg_end in [(start, mid), (mid, end)]:
                seg = "\n".join(lines[seg_start:seg_end])
                chunks.append(_make_chunk(
                    filepath, seg, "python", chunk_type,
                    seg_start + 1, seg_end,
                ))
        else:
            chunks.append(_make_chunk(
                filepath, body, "python", chunk_type,
                start + 1, end,
            ))

    # If AST produced no top-level definitions, fall back to line chunks
    if not chunks:
        return _line_chunks(filepath, source, language="python")

    return chunks


def _line_chunks(filepath: str, source: str, language: str = "") -> list[dict]:
    """Sliding-window line chunker for non-Python files."""
    lines = source.splitlines()
    chunks = []
    i = 0
    while i < len(lines):
        window = lines[i : i + LINE_CHUNK]
        content = "\n".join(window)
        if content.strip():
            chunks.append(_make_chunk(
                filepath, content, language, "module",
                i + 1, min(i + LINE_CHUNK, len(lines)),
            ))
        i += LINE_CHUNK - LINE_OVERLAP
    return chunks


def _make_chunk(
    filepath: str,
    content: str,
    language: str,
    chunk_type: str,
    line_start: int,
    line_end: int,
) -> dict:
    return {
        "filepath": filepath,
        "content": content,
        "language": language,
        "chunk_type": chunk_type,
        "line_start": line_start,
        "line_end": line_end,
    }


# ── File walker ────────────────────────────────────────────────────────────────

def _should_skip(path: Path, extensions: set[str]) -> bool:
    if any(part in IGNORED_DIRS for part in path.parts):
        return True
    if path.suffix not in extensions:
        return True
    if any(str(path).endswith(s) for s in IGNORED_SUFFIXES):
        return True
    return False


def collect_chunks(repo_path: Path, extensions: set[str], repo_name: str) -> list[dict]:
    """Walk the repo and produce all document chunks."""
    all_chunks: list[dict] = []

    for file_path in sorted(repo_path.rglob("*")):
        if not file_path.is_file():
            continue
        if _should_skip(file_path.relative_to(repo_path), extensions):
            continue

        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Could not read file", path=str(file_path), error=str(exc))
            continue

        if not source.strip():
            continue

        # Use repo-relative path as the filepath field
        rel_path = str(file_path.relative_to(repo_path))
        language = LANGUAGE_MAP.get(file_path.suffix, file_path.suffix.lstrip("."))

        if file_path.suffix == ".py":
            chunks = _python_chunks(rel_path, source)
        else:
            chunks = _line_chunks(rel_path, source, language)

        for chunk in chunks:
            chunk["repo"] = repo_name

        all_chunks.extend(chunks)

    return all_chunks


# ── Entry point ────────────────────────────────────────────────────────────────

async def main(repo_path: Path, extensions: set[str], dry_run: bool) -> None:
    repo_name = repo_path.name
    logger.info("Collecting code chunks", repo=str(repo_path), repo_name=repo_name)

    chunks = collect_chunks(repo_path, extensions, repo_name)
    logger.info("Chunks collected", total=len(chunks))

    if dry_run:
        for c in chunks[:5]:
            print(f"\n{'─'*60}")
            print(f"File: {c['filepath']} ({c['language']}, {c['chunk_type']})")
            print(f"Lines: {c['line_start']}-{c['line_end']}")
            print(c["content"][:300])
        print(f"\n... {len(chunks)} total chunks (dry run — not uploading)")
        return

    # Ensure the index exists
    logger.info("Creating/updating index schema")
    manager = IndexManager()
    await manager.create_or_update_indexes()

    # Upload
    logger.info("Uploading to Azure AI Search", index=settings.azure_search_code_index)
    async with Indexer(index_name=settings.azure_search_code_index) as indexer:
        uploaded = await indexer.upload(chunks)

    logger.info("Indexing complete", uploaded=uploaded, total=len(chunks))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Index a repository into Azure AI Search")
    parser.add_argument("--repo", required=True, help="Path to the repository root")
    parser.add_argument("--ext", nargs="+", default=None, help="File extensions to index")
    parser.add_argument("--dry-run", action="store_true", help="Print chunks without uploading")
    args = parser.parse_args()

    exts = set(args.ext) if args.ext else DEFAULT_EXTENSIONS
    # Ensure extensions have a leading dot
    exts = {e if e.startswith(".") else f".{e}" for e in exts}

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"Error: {repo} is not a directory", file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(repo, exts, args.dry_run))
