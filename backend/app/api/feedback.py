"""
POST /api/feedback          — save SME rating for a response
GET  /api/feedback/trends   — daily satisfaction trend data for the Dashboard
"""
from __future__ import annotations

from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from app.api.deps import get_current_user, get_feedback_store
from app.limiter import limiter
from app.memory.cosmos_store import FeedbackStore

logger = structlog.get_logger()
router = APIRouter(tags=["feedback"])

_MAX_CORRECTION_LENGTH = 2_000


class FeedbackRequest(BaseModel):
    sessionId: str
    messageId: str
    rating: Literal[1, -1]                  # 1 = thumbs up, -1 = thumbs down
    correction: str | None = None
    storyId: str | None = None

    @field_validator("sessionId", "messageId")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v.strip()

    @field_validator("correction")
    @classmethod
    def correction_length(cls, v: str | None) -> str | None:
        if v is not None and len(v) > _MAX_CORRECTION_LENGTH:
            raise ValueError(
                f"Correction text exceeds {_MAX_CORRECTION_LENGTH} characters."
            )
        return v


class FeedbackResponse(BaseModel):
    id: str
    status: str


@router.post("/feedback", response_model=FeedbackResponse)
@limiter.limit("60/minute")
async def submit_feedback(
    request: Request,
    body: FeedbackRequest,
    user: Annotated[dict, Depends(get_current_user)],
    store: Annotated[FeedbackStore, Depends(get_feedback_store)],
):
    doc_id = await store.save_feedback(
        session_id=body.sessionId,
        message_id=body.messageId,
        rating=body.rating,
        correction=body.correction,
        story_id=body.storyId,
        user_id=user["sub"],
    )
    logger.info(
        "Feedback saved",
        session_id=body.sessionId,
        message_id=body.messageId,
        rating=body.rating,
        has_correction=body.correction is not None,
        user=user.get("preferred_username"),
    )
    return FeedbackResponse(id=doc_id, status="saved")


@router.get("/feedback/trends")
@limiter.limit("30/minute")
async def get_trends(
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
    store: Annotated[FeedbackStore, Depends(get_feedback_store)],
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
):
    rows = await store.get_score_trends(days=days)

    # Bucket by calendar day (UTC)
    buckets: dict[str, dict] = {}
    for row in rows:
        day_ts = row["timestamp"] // 86400 * 86400
        day_key = str(day_ts)
        if day_key not in buckets:
            buckets[day_key] = {
                "date": day_key,
                "up": 0,
                "down": 0,
                "total": 0,
            }
        if row["rating"] == 1:
            buckets[day_key]["up"] += 1
        else:
            buckets[day_key]["down"] += 1
        buckets[day_key]["total"] += 1

    trend = sorted(buckets.values(), key=lambda x: x["date"])
    for point in trend:
        total = point["total"]
        point["satisfaction"] = round(point["up"] / total * 100, 1) if total else 0.0

    return {"days": days, "trend": trend}
