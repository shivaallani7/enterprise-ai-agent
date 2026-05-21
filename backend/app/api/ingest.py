"""
Document ingestion API.

POST /api/ingest          — upload one or more files; returns per-file IngestReport
GET  /api/ingest/registry — list all previously ingested documents
DELETE /api/ingest/registry/{filename} — remove from index + registry
"""
from __future__ import annotations

import dataclasses
from typing import List

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.api.deps import get_current_user
from app.integrations.rag_ingestor import RagIngestor
from app.memory.cosmos_store import IngestRegistry

logger = structlog.get_logger()
router = APIRouter(prefix="/ingest", tags=["ingest"])

# One ingestor instance per worker process (OpenAI client is reused across requests)
_ingestor = RagIngestor()


@router.post("")
async def ingest_documents(
    files: List[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
):
    """
    Upload one or more documents for RAG ingestion.
    Returns a structured IngestReport per file.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 files per request")

    reports = []
    for upload in files:
        filename = upload.filename or "unnamed"
        try:
            data = await upload.read()
            report = await _ingestor.ingest(data, filename)
            reports.append(dataclasses.asdict(report))
            logger.info(
                "File ingested",
                file=filename,
                status=report.status,
                chunks=report.chunks_created,
                ms=report.processing_ms,
                user=user.get("sub"),
            )
        except Exception as exc:
            logger.error("Ingestion error", file=filename, error=str(exc), exc_info=True)
            reports.append({
                "doc_id": "",
                "file_name": filename,
                "doc_type": "",
                "mode": "INSERT",
                "status": "FAILED",
                "chunks_created": 0,
                "chunks_skipped": 0,
                "chunks_failed": 0,
                "embedding_model": "",
                "chunk_strategy": "",
                "chunk_size": 512,
                "overlap_pct": 20,
                "version": 1,
                "processing_ms": 0,
                "warnings": [],
                "indexed_at": "",
                "error": str(exc),
            })

    return {"reports": reports, "total": len(reports)}


@router.get("/registry")
async def list_ingested(user: dict = Depends(get_current_user)):
    """List all documents currently in the ingest registry."""
    registry = IngestRegistry()
    try:
        items = await registry.list_all()
    except Exception as exc:
        logger.error("Registry list failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Registry error: {exc}")
    return {"documents": items, "count": len(items)}


@router.delete("/registry/{filename:path}")
async def delete_ingested(filename: str, user: dict = Depends(get_current_user)):
    """
    Delete a document from the vector index and the registry.
    Uses the filename as the registry key.
    """
    registry = IngestRegistry()
    record = await registry.lookup(filename)
    if not record:
        raise HTTPException(status_code=404, detail=f"Document '{filename}' not found in registry")

    doc_id = record["doc_id"]

    # Delete vectors from Azure AI Search
    try:
        await _ingestor._delete_by_doc_id(doc_id)
    except Exception as exc:
        logger.warning("Vector deletion partial", doc_id=doc_id, error=str(exc))

    # Remove from registry
    await registry.delete(filename)

    logger.info("Document deleted", filename=filename, doc_id=doc_id, user=user.get("sub"))
    return {"deleted": True, "filename": filename, "doc_id": doc_id}
