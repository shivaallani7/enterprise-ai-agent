"""
POST /api/chat        — SSE streaming chat
GET  /api/chat/history/{session_id} — message history for a session
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

from app.agents.orchestrator import OrchestratorAgent
from app.agents.prompts import build_story_prompt, build_general_prompt
from app.api.deps import get_current_user, get_session_store, get_user_store
from app.integrations.jira_client import JiraClient, JiraNotFoundError
from app.limiter import limiter
from app.memory.cosmos_store import SessionStore, UserStore
from app.models.user import PERSONA_INSTRUCTIONS

logger = structlog.get_logger()
router = APIRouter(tags=["chat"])

_STORY_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
_MAX_MESSAGE_LENGTH = 8_000   # characters per message
_MAX_HISTORY_MESSAGES = 40    # messages in a single request

_orchestrator: OrchestratorAgent | None = None
_jira_client: JiraClient | None = None


def get_orchestrator() -> OrchestratorAgent:
    global _orchestrator
    if not _orchestrator:
        _orchestrator = OrchestratorAgent()
    return _orchestrator


def get_jira_client() -> JiraClient:
    global _jira_client
    if not _jira_client:
        _jira_client = JiraClient()
    return _jira_client


# ── Request / Response models ─────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, v: str) -> str:
        if v not in ("user", "assistant", "system"):
            raise ValueError(f"Invalid role '{v}'. Must be user, assistant, or system.")
        return v

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message content must not be empty.")
        if len(v) > _MAX_MESSAGE_LENGTH:
            raise ValueError(
                f"Message content exceeds {_MAX_MESSAGE_LENGTH} characters."
            )
        return v


class ChatRequest(BaseModel):
    sessionId: str
    storyId: str | None = None
    messages: list[ChatMessage]

    @field_validator("sessionId")
    @classmethod
    def session_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("sessionId must not be empty.")
        return v.strip()

    @field_validator("storyId")
    @classmethod
    def story_id_format(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if not _STORY_KEY_RE.match(v):
            raise ValueError(
                f"storyId '{v}' is not a valid Jira key (expected pattern: PROJ-123)."
            )
        return v

    @model_validator(mode="after")
    def messages_not_empty(self) -> "ChatRequest":
        if not self.messages:
            raise ValueError("messages must not be empty.")
        if len(self.messages) > _MAX_HISTORY_MESSAGES:
            raise ValueError(
                f"Too many messages ({len(self.messages)}). "
                f"Maximum is {_MAX_HISTORY_MESSAGES}."
            )
        # Last message must be from user
        if self.messages[-1].role != "user":
            raise ValueError("The last message must have role 'user'.")
        return self


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/chat")
@limiter.limit("20/minute")
async def chat(
    request: Request,                   # required by slowapi
    body: ChatRequest,
    user: Annotated[dict, Depends(get_current_user)],
    store: Annotated[SessionStore, Depends(get_session_store)],
    user_store: Annotated[UserStore, Depends(get_user_store)],
):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    user_id = user["sub"]
    start_ts = time.perf_counter()

    # ── Load user profile (auto-creates if first visit) ───────────────────────
    profile = await user_store.get_or_create(
        sub=user_id,
        email=user.get("preferred_username", ""),
        name=user.get("name", user.get("preferred_username", "")),
        default_persona=user.get("default_persona", "general"),
    )
    persona = profile.get("persona", "general")
    persona_instructions = PERSONA_INSTRUCTIONS.get(persona, "")

    logger.info(
        "Chat request received",
        request_id=request_id,
        session_id=body.sessionId,
        story_id=body.storyId,
        message_count=len(body.messages),
        user=user.get("preferred_username"),
        persona=persona,
    )

    orchestrator = get_orchestrator()
    jira = get_jira_client()

    # ── Build system prompt ───────────────────────────────────────────────────
    if body.storyId:
        try:
            story = await jira.get_story(body.storyId)
            system_prompt = build_story_prompt(story.to_dict(), persona_instructions)
        except JiraNotFoundError:
            raise HTTPException(
                status_code=404,
                detail=f"Jira story {body.storyId} not found. Check the story key.",
            )
        except Exception as exc:
            logger.warning(
                "Story context fetch failed — falling back to general prompt",
                story_id=body.storyId,
                error=str(exc),
            )
            system_prompt = build_general_prompt(persona_instructions=persona_instructions)
    else:
        system_prompt = build_general_prompt(persona_instructions=persona_instructions)

    # ── Persist user message ──────────────────────────────────────────────────
    user_message_id = str(uuid.uuid4())
    await store.save_message(
        user_id=user_id,
        session_id=body.sessionId,
        role="user",
        content=body.messages[-1].content,
        metadata={"messageId": user_message_id, "requestId": request_id},
    )

    # ── SSE event generator ───────────────────────────────────────────────────
    async def event_generator():
        assistant_parts: list[str] = []
        final_sources: list[str] = []

        try:
            async for chunk in orchestrator.stream_response(
                messages=[m.model_dump() for m in body.messages],
                system_prompt=system_prompt,
                session_id=body.sessionId,
            ):
                is_done = chunk.get("done", False)

                if is_done:
                    final_sources = chunk.get("sources", [])
                else:
                    assistant_parts.append(chunk["delta"])

                yield {
                    "data": json.dumps({
                        "delta": chunk["delta"],
                        "sources": chunk.get("sources", []),
                        "confidence": chunk.get("confidence", 0.9),
                        "done": is_done,
                        "requestId": request_id,
                    })
                }

        except Exception as exc:
            logger.error(
                "Stream failed",
                request_id=request_id,
                session_id=body.sessionId,
                error=str(exc),
                exc_info=True,
            )
            yield {
                "data": json.dumps({
                    "delta": "An error occurred. Please try again.",
                    "sources": [],
                    "confidence": 0.0,
                    "done": True,
                    "requestId": request_id,
                })
            }
            return

        # Persist assistant message after stream completes
        full_response = "".join(assistant_parts)
        await store.save_message(
            user_id=user_id,
            session_id=body.sessionId,
            role="assistant",
            content=full_response,
            metadata={
                "messageId": str(uuid.uuid4()),
                "requestId": request_id,
                "sources": final_sources,
            },
        )

        duration_ms = round((time.perf_counter() - start_ts) * 1000)
        logger.info(
            "Chat stream completed",
            request_id=request_id,
            session_id=body.sessionId,
            response_chars=len(full_response),
            sources_count=len(final_sources),
            duration_ms=duration_ms,
        )

    return EventSourceResponse(event_generator())


@router.get("/chat/history/{session_id}")
@limiter.limit("60/minute")
async def get_history(
    request: Request,
    session_id: str,
    user: Annotated[dict, Depends(get_current_user)],
    store: Annotated[SessionStore, Depends(get_session_store)],
):
    history = await store.get_history(user["sub"], session_id)
    return {"sessionId": session_id, "messages": history}
