"""
FastAPI dependencies: auth token validation, shared stores.

Auth uses username/password tokens issued by POST /api/auth/login.
Tokens have the form "dev-<username>" and are validated against DEV_USERS in auth.py.
"""
from __future__ import annotations

import re

import structlog
from fastapi import HTTPException, Header

from app.memory.cosmos_store import SessionStore, FeedbackStore, UserStore

logger = structlog.get_logger()

# ── Store singletons ──────────────────────────────────────────────────────────

_session_store: SessionStore | None = None
_feedback_store: FeedbackStore | None = None
_user_store: UserStore | None = None


def get_session_store() -> SessionStore:
    global _session_store
    if not _session_store:
        _session_store = SessionStore()
    return _session_store


def get_feedback_store() -> FeedbackStore:
    global _feedback_store
    if not _feedback_store:
        _feedback_store = FeedbackStore()
    return _feedback_store


def get_user_store() -> UserStore:
    global _user_store
    if not _user_store:
        _user_store = UserStore()
    return _user_store


# ── Auth dependencies ─────────────────────────────────────────────────────────

async def get_current_user(
    authorization: str = Header(..., description="Bearer dev-<username>"),
) -> dict:
    """
    Validate a username/password token issued by POST /api/auth/login.
    Token format: "dev-<username>"
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header must be 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()

    if token.startswith("dev-"):
        from app.api.auth import DEV_USERS
        username = re.sub(r"[^a-zA-Z0-9_-]", "", token[4:])[:32] or "user"
        user_record = DEV_USERS.get(username, {})
        if not user_record:
            raise HTTPException(status_code=401, detail="Invalid token.")
        return {
            "sub": f"dev-{username}",
            "preferred_username": f"{username}@dev.local",
            "name": user_record.get("display_name", username.title()),
            "default_persona": user_record.get("default_persona", "general"),
        }

    raise HTTPException(
        status_code=401,
        detail="Invalid token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_optional_user(
    authorization: str = Header(default=""),
) -> dict | None:
    """Like get_current_user but returns None instead of raising 401."""
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None
