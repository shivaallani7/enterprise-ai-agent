"""
GET /api/jira/stories               — open stories assigned to the authenticated user
GET /api/jira/stories/{key}         — full story detail (cached, refreshes every 5 min)
GET /api/jira/stories/{key}/context — rendered system prompt for a story tab (dev/debug)
DELETE /api/jira/stories/{key}/cache — force-expire the story cache entry
"""
from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.agents.prompts import build_story_prompt
from app.api.deps import get_current_user
from app.config import get_settings
from app.integrations.jira_client import (
    JiraClient,
    JiraAuthError,
    JiraNotFoundError,
    JiraPermissionError,
    JiraRateLimitError,
)
from app.limiter import limiter

logger = structlog.get_logger()
router = APIRouter(tags=["jira"])
settings = get_settings()

_jira: JiraClient | None = None


def get_jira() -> JiraClient:
    global _jira
    if not _jira:
        _jira = JiraClient()
    return _jira


def _jira_exc_to_http(exc: Exception, detail_prefix: str = "") -> HTTPException:
    """Convert typed Jira exceptions to appropriate HTTP status codes."""
    prefix = f"{detail_prefix}: " if detail_prefix else ""
    if isinstance(exc, JiraAuthError):
        return HTTPException(status_code=502, detail=f"{prefix}{exc}")
    if isinstance(exc, JiraPermissionError):
        return HTTPException(status_code=502, detail=f"{prefix}{exc}")
    if isinstance(exc, JiraNotFoundError):
        return HTTPException(status_code=404, detail=f"{prefix}{exc}")
    if isinstance(exc, JiraRateLimitError):
        return HTTPException(
            status_code=429,
            detail=f"{prefix}{exc}",
            headers={"Retry-After": "60"},
        )
    return HTTPException(status_code=502, detail=f"{prefix}Jira API error: {exc}")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/jira/stories")
@limiter.limit("30/minute")
async def list_stories(
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
):
    """
    Returns open Jira stories assigned to the authenticated user.

    The `sub` claim from the Entra ID token is used as the Jira account ID.
    In dev mode (sub = 'dev-user'), falls back to `currentUser()` JQL so
    the Jira API returns stories for the service account credentials.
    """
    account_id = user.get("sub", "currentUser()")
    if account_id.startswith("dev-"):   # any dev-* token → use service account
        account_id = "currentUser()"

    jira = get_jira()
    try:
        stories = await jira.get_my_open_stories(account_id)
        return {
            "stories": [s.to_dict() for s in stories],
            "count": len(stories),
        }
    except Exception as exc:
        logger.error("Failed to list Jira stories", account_id=account_id, error=str(exc))
        raise _jira_exc_to_http(exc, "Could not fetch stories")


@router.get("/jira/stories/{story_key}")
@limiter.limit("30/minute")
async def get_story(
    request: Request,
    story_key: str = Path(
        ...,
        description="Jira story key, e.g. PROJ-123",
        pattern=r"^[A-Z][A-Z0-9]+-\d+$",
    ),
    user: Annotated[dict, Depends(get_current_user)] = None,
):
    """
    Returns full story detail. Results are cached for the configured TTL
    (default 5 minutes) so repeated calls within a chat session are cheap.
    """
    story_key = story_key.upper()
    jira = get_jira()
    try:
        story = await jira.get_story(story_key)
        return story.to_dict()
    except Exception as exc:
        logger.error("Failed to fetch story", story_key=story_key, error=str(exc))
        raise _jira_exc_to_http(exc, f"Could not fetch {story_key}")


@router.get("/jira/stories/{story_key}/context")
@limiter.limit("30/minute")
async def get_story_context(
    request: Request,
    story_key: str = Path(
        ...,
        description="Jira story key, e.g. PROJ-123",
        pattern=r"^[A-Z][A-Z0-9]+-\d+$",
    ),
    user: Annotated[dict, Depends(get_current_user)] = None,
):
    """
    Returns the rendered system prompt that will be injected for this story tab.

    Useful for debugging prompt content and verifying ADF extraction before
    running a full chat session. Only available in non-production environments.
    """
    if settings.app_environment == "production":
        raise HTTPException(status_code=404, detail="Not found.")

    story_key = story_key.upper()
    jira = get_jira()
    try:
        story = await jira.get_story(story_key)
        prompt = build_story_prompt(story.to_dict())
        return {
            "story_key": story_key,
            "system_prompt": prompt,
            "prompt_length": len(prompt),
        }
    except Exception as exc:
        logger.error("Failed to build story context", story_key=story_key, error=str(exc))
        raise _jira_exc_to_http(exc, f"Could not build context for {story_key}")


@router.delete("/jira/stories/{story_key}/cache", status_code=204)
async def invalidate_story_cache(
    story_key: str = Path(
        ...,
        description="Jira story key, e.g. PROJ-123",
        pattern=r"^[A-Z][A-Z0-9]+-\d+$",
    ),
    user: Annotated[dict, Depends(get_current_user)] = None,
):
    """
    Force-expire the cached story entry. Call this after updating a story
    in Jira to get fresh context on the next chat request without waiting
    for the TTL to expire.
    """
    story_key = story_key.upper()
    get_jira().invalidate_cache(story_key)
    logger.info("Story cache invalidated", story_key=story_key, user=user.get("sub"))
    # 204 No Content — no body
