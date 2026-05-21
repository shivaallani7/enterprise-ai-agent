"""
Index documentation (Markdown, plain text, RST) into the Azure AI Search docs-index.

Usage
─────
    # From the backend/ directory:
    python -m scripts.index_docs --docs /path/to/docs

    # Include a live repo README:
    python -m scripts.index_docs --docs /path/to/docs --repo /path/to/repo

    # Dry run:
    python -m scripts.index_docs --docs /path/to/docs --dry-run

Chunking strategy
─────────────────
Markdown: split at heading boundaries (# / ## / ###).
  Each section becomes one chunk with the heading as `title`.
  Sections > MAX_CHARS are split at the nearest blank line.

Plain text / RST: sliding-window paragraph chunker — split at double
  newlines, accumulate until MAX_CHARS, then flush.

Min chunk size: MIN_CHARS characters. Chunks below this are discarded
  (e.g. empty sections, navigation-only pages).
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog

from app.config import get_settings
from app.integrations.indexer import Indexer
from app.integrations.search_client import IndexManager

logger = structlog.get_logger()
settings = get_settings()

# ── Configuration ──────────────────────────────────────────────────────────────

DEFAULT_EXTENSIONS = {".md", ".mdx", ".txt", ".rst"}
IGNORED_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".venv"}
MAX_CHARS = 3_000
MIN_CHARS = 100

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


# ── Markdown chunker ───────────────────────────────────────────────────────────

def _markdown_chunks(filepath: str, content: str) -> list[dict]:
    """Split markdown at heading boundaries."""
    matches = list(_HEADING_RE.finditer(content))
    if not matches:
        return _paragraph_chunks(filepath, content, chunk_type="prose")

    chunks = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        section = content[start:end].strip()

        if len(section) < MIN_CHARS:
            continue

        title = match.group(2).strip()

        if len(section) > MAX_CHARS:
            # Split oversized section at blank lines
            for part in _split_at_blank_lines(section, MAX_CHARS):
                if len(part) >= MIN_CHARS:
                    chunks.append(_make_doc(filepath, part, title, "section"))
        else:
            chunks.append(_make_doc(filepath, section, title, "section"))

    return chunks if chunks else _paragraph_chunks(filepath, content)


def _split_at_blank_lines(text: str, max_chars: int) -> list[str]:
    """Split text at blank lines, keeping chunks ≤ max_chars."""
    paragraphs = re.split(r"\n\s*\n", text)
    parts: list[str] = []
    buf = ""
    for para in paragraphs:
        candidate = (buf + "\n\n" + para).strip() if buf else para
        if len(candidate) > max_chars and buf:
            parts.append(buf.strip())
            buf = para
        else:
            buf = candidate
    if buf.strip():
        parts.append(buf.strip())
    return parts


def _paragraph_chunks(filepath: str, content: str, chunk_type: str = "prose") -> list[dict]:
    """Accumulate paragraphs into chunks ≤ MAX_CHARS."""
    paragraphs = re.split(r"\n\s*\n", content)
    chunks: list[dict] = []
    buf = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        candidate = (buf + "\n\n" + para).strip() if buf else para
        if len(candidate) > MAX_CHARS and buf:
            if len(buf) >= MIN_CHARS:
                chunks.append(_make_doc(filepath, buf, _infer_title(buf, filepath), chunk_type))
            buf = para
        else:
            buf = candidate
    if buf.strip() and len(buf) >= MIN_CHARS:
        chunks.append(_make_doc(filepath, buf, _infer_title(buf, filepath), chunk_type))
    return chunks


def _infer_title(content: str, filepath: str) -> str:
    """Use first non-empty line as title, fall back to filename."""
    first_line = content.split("\n")[0].lstrip("#").strip()
    return first_line if first_line else Path(filepath).stem


def _make_doc(filepath: str, content: str, title: str, chunk_type: str) -> dict:
    return {
        "filepath": filepath,
        "title": title,
        "content": content,
        "chunk_type": chunk_type,
        "source": "documentation",
    }


# ── File walker ────────────────────────────────────────────────────────────────

def _should_skip(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path.parts)


def collect_chunks(docs_path: Path, extensions: set[str]) -> list[dict]:
    all_chunks: list[dict] = []

    for file_path in sorted(docs_path.rglob("*")):
        if not file_path.is_file():
            continue
        if _should_skip(file_path.relative_to(docs_path)):
            continue
        if file_path.suffix.lower() not in extensions:
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Could not read file", path=str(file_path), error=str(exc))
            continue

        if not content.strip():
            continue

        rel_path = str(file_path.relative_to(docs_path))

        if file_path.suffix.lower() in (".md", ".mdx"):
            chunks = _markdown_chunks(rel_path, content)
        else:
            chunks = _paragraph_chunks(rel_path, content)

        all_chunks.extend(chunks)

    return all_chunks


# ── Entry point ────────────────────────────────────────────────────────────────

async def main(docs_path: Path, repo_path: Path | None, dry_run: bool) -> None:
    logger.info("Collecting documentation chunks", docs=str(docs_path))
    chunks = collect_chunks(docs_path, DEFAULT_EXTENSIONS)

    # Optionally also index the repo's own README and doc files
    if repo_path and repo_path != docs_path:
        repo_chunks = collect_chunks(repo_path, DEFAULT_EXTENSIONS)
        # Prefix repo-relative paths with "repo/"
        for c in repo_chunks:
            c["filepath"] = f"repo/{c['filepath']}"
            c["source"] = "repo"
        chunks.extend(repo_chunks)

    logger.info("Chunks collected", total=len(chunks))

    if dry_run:
        for c in chunks[:5]:
            print(f"\n{'─'*60}")
            print(f"File: {c['filepath']} | Title: {c['title']}")
            print(c["content"][:300])
        print(f"\n... {len(chunks)} total chunks (dry run — not uploading)")
        return

    logger.info("Creating/updating index schema")
    await IndexManager().create_or_update_indexes()

    logger.info("Uploading to Azure AI Search", index=settings.azure_search_docs_index)
    async with Indexer(index_name=settings.azure_search_docs_index) as indexer:
        uploaded = await indexer.upload(chunks)

    logger.info("Indexing complete", uploaded=uploaded, total=len(chunks))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Index documentation into Azure AI Search")
    parser.add_argument("--docs", required=True, help="Path to documentation directory")
    parser.add_argument("--repo", default=None, help="Also index README/docs from a repo root")
    parser.add_argument("--dry-run", action="store_true", help="Print chunks without uploading")
    args = parser.parse_args()

    docs = Path(args.docs).resolve()
    if not docs.is_dir():
        print(f"Error: {docs} is not a directory", file=sys.stderr)
        sys.exit(1)

    repo = Path(args.repo).resolve() if args.repo else None

    asyncio.run(main(docs, repo, args.dry_run))
