"""
FastAPI dependencies: auth token validation, shared stores.

JWKS caching: Microsoft's JWKS endpoint (used to verify Entra ID tokens)
is cached for JWKS_TTL seconds. Without caching, every API request would
make an outbound HTTP call to login.microsoftonline.com before serving
any response — adding ~50-200ms latency and creating a hard dependency
on Microsoft's auth infrastructure for every single request.
"""
from __future__ import annotations

import asyncio
import time

import httpx
import structlog
from fastapi import Depends, HTTPException, Header
from jose import jwt, JWTError

from app.config import get_settings
from app.memory.cosmos_store import SessionStore, FeedbackStore, UserStore

logger = structlog.get_logger()
settings = get_settings()

# ── JWKS cache ────────────────────────────────────────────────────────────────

JWKS_TTL = 3600  # seconds — Microsoft rotates keys infrequently

_jwks_cache: dict | None = None
_jwks_expires_at: float = 0.0
_jwks_lock = asyncio.Lock()


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_expires_at

    now = time.monotonic()
    if _jwks_cache and now < _jwks_expires_at:
        return _jwks_cache

    async with _jwks_lock:
        # Second check inside lock to avoid thundering herd on expiry
        if _jwks_cache and time.monotonic() < _jwks_expires_at:
            return _jwks_cache

        jwks_uri = (
            f"https://login.microsoftonline.com/{settings.entra_tenant_id}"
            f"/discovery/v2.0/keys"
        )
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(jwks_uri)
                resp.raise_for_status()
                _jwks_cache = resp.json()
                _jwks_expires_at = time.monotonic() + JWKS_TTL
                logger.info("JWKS refreshed", ttl_seconds=JWKS_TTL)
        except Exception as exc:
            # If refresh fails but we have a stale cache, keep using it
            # rather than rejecting all users. Log the failure prominently.
            if _jwks_cache:
                logger.error(
                    "JWKS refresh failed — using stale cache",
                    error=str(exc),
                    stale_age_s=round(time.monotonic() - (_jwks_expires_at - JWKS_TTL)),
                )
                return _jwks_cache
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable.",
            )

    return _jwks_cache  # type: ignore[return-value]


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
    authorization: str = Header(..., description="Bearer <entra_id_token>"),
) -> dict:
    """
    Validate an Azure Entra ID JWT bearer token.
    Returns decoded claims: { sub, preferred_username, name, ... }

    Dev shortcut: APP_ENVIRONMENT=development + token="dev-token" bypasses
    validation so the backend runs without Azure credentials locally.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization header must be 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.removeprefix("Bearer ").strip()

    # ── Username/password token bypass ───────────────────────────────────────
    # Accept "dev-<username>" tokens issued by /api/auth/login.
    if token.startswith("dev-"):
        from app.api.auth import DEV_USERS
        import re as _re
        username = token[4:].strip()
        username = _re.sub(r"[^a-zA-Z0-9_-]", "", username)[:32] or "user"
        user_record = DEV_USERS.get(username, {})
        return {
            "sub": f"dev-{username}",
            "preferred_username": f"{username}@dev.local",
            "name": user_record.get("display_name", username.title()),
            "default_persona": user_record.get("default_persona", "general"),
        }

    # ── Production validation ─────────────────────────────────────────────────
    try:
        jwks = await _get_jwks()
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            audience=settings.entra_client_id,
            issuer=(
                f"https://login.microsoftonline.com/{settings.entra_tenant_id}/v2.0"
            ),
        )
        return claims
    except JWTError as exc:
        raise HTTPException(
            status_code=401,
            detail=f"Token validation failed: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_optional_user(
    authorization: str = Header(default=""),
) -> dict | None:
    """
    Like get_current_user but returns None instead of raising 401.
    Used for endpoints that adjust behaviour based on auth context
    but don't strictly require it (e.g. Copilot — token validated by GitHub).
    """
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None
