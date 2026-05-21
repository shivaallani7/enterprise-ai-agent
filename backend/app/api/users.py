"""
GET  /api/users/me          — get or auto-create the current user's profile
PUT  /api/users/me          — update name / persona
GET  /api/users/me/sessions — list the user's past chat sessions
"""
from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_current_user, get_session_store, get_user_store
from app.limiter import limiter
from app.memory.cosmos_store import SessionStore, UserStore
from app.models.user import PERSONA_LABELS, UserProfileUpdate

logger = structlog.get_logger()
router = APIRouter(tags=["users"])


@router.get("/users/me")
@limiter.limit("60/minute")
async def get_me(
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
    store: Annotated[UserStore, Depends(get_user_store)],
):
    profile = await store.get_or_create(
        sub=user["sub"],
        email=user.get("preferred_username", ""),
        name=user.get("name", user.get("preferred_username", "")),
        default_persona=user.get("default_persona", "general"),
    )
    return {
        "sub": profile["sub"],
        "email": profile.get("email", ""),
        "name": profile.get("name", ""),
        "persona": profile.get("persona", "general"),
        "personaLabel": PERSONA_LABELS.get(profile.get("persona", "general"), "General"),
        "createdAt": profile.get("created_at", 0),
        "updatedAt": profile.get("updated_at", 0),
    }


@router.put("/users/me")
@limiter.limit("30/minute")
async def update_me(
    request: Request,
    body: UserProfileUpdate,
    user: Annotated[dict, Depends(get_current_user)],
    store: Annotated[UserStore, Depends(get_user_store)],
):
    updates: dict = {}
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="name must not be empty")
        updates["name"] = name
    if body.persona is not None:
        if body.persona not in PERSONA_LABELS:
            raise HTTPException(status_code=422, detail=f"Invalid persona: {body.persona}")
        updates["persona"] = body.persona

    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    try:
        profile = await store.update(user["sub"], updates)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "sub": profile["sub"],
        "email": profile.get("email", ""),
        "name": profile.get("name", ""),
        "persona": profile.get("persona", "general"),
        "personaLabel": PERSONA_LABELS.get(profile.get("persona", "general"), "General"),
        "updatedAt": profile.get("updated_at", 0),
    }


@router.get("/users/me/sessions")
@limiter.limit("30/minute")
async def get_my_sessions(
    request: Request,
    user: Annotated[dict, Depends(get_current_user)],
    user_store: Annotated[UserStore, Depends(get_user_store)],
    session_store: Annotated[SessionStore, Depends(get_session_store)],
):
    sessions = await user_store.get_sessions(user["sub"], session_store)
    return {"sessions": sessions, "count": len(sessions)}
