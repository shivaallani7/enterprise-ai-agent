"""
POST /api/copilot          — GitHub Copilot extension agent endpoint
GET  /api/copilot/manifest — Extension capability manifest

GitHub Copilot Extensions protocol
───────────────────────────────────
GitHub forwards user messages to this endpoint with:
  - X-GitHub-Token: a short-lived token identifying the GitHub user
  - Body: { messages, copilot_thread_id, agent, context }

We verify the token is non-empty, then call the same OrchestratorAgent as
Product 1, injecting IDE context (active file, selection, branch) into the
system prompt. The response is an OpenAI-compatible SSE stream terminated
with `data: [DONE]`.

get_jira_story_for_branch is handled directly here (fast path): if the
branch name matches feature/PROJ-123*, we pre-fetch the Jira story and
inject it into the system prompt before the agent is invoked. The SK
JiraContextPlugin can still call get_story mid-stream for other stories.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from app.agents.orchestrator import OrchestratorAgent
from app.agents.prompts import build_copilot_prompt
from app.config import get_settings
from app.integrations.jira_client import JiraClient

logger = structlog.get_logger()
router = APIRouter(tags=["copilot"])
settings = get_settings()

BRANCH_STORY_RE = re.compile(r"(?:feature|fix|chore)/([A-Z][A-Z0-9]+-\d+)", re.IGNORECASE)

_orchestrator: OrchestratorAgent | None = None
_jira: JiraClient | None = None


def _get_orchestrator() -> OrchestratorAgent:
    global _orchestrator
    if not _orchestrator:
        _orchestrator = OrchestratorAgent()
    return _orchestrator


def _get_jira() -> JiraClient:
    global _jira
    if not _jira:
        _jira = JiraClient()
    return _jira


# ── Request / Response models ─────────────────────────────────────────────────

class CopilotContext(BaseModel):
    activeFile: str = ""
    selection: str = ""
    repoName: str = ""
    branch: str = ""


class CopilotRequest(BaseModel):
    messages: list[dict]
    context: CopilotContext = CopilotContext()
    model: str = "gpt-4o"                   # Copilot extensibility spec field
    copilot_thread_id: str = ""


# ── GitHub token verification ─────────────────────────────────────────────────

async def _verify_github_token(token: str) -> dict:
    """
    Verify the X-GitHub-Token by calling the GitHub API.
    Returns the GitHub user object on success.

    For private GitHub Apps, the token is a short-lived Copilot token.
    We verify it's valid by calling /user — if GitHub returns 200,
    the token is legitimate.

    Skip in development mode so you can test without a real GitHub token.
    """
    if settings.app_environment == "development":
        return {"login": "dev-user", "id": 0}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if resp.status_code == 401:
                raise HTTPException(status_code=401, detail="Invalid GitHub token.")
            resp.raise_for_status()
            return resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        # Network failure — fail open in non-production to avoid blocking devs.
        # In production, fail closed.
        logger.warning("GitHub token verification failed", error=str(exc))
        if settings.app_environment == "production":
            raise HTTPException(
                status_code=503,
                detail="Could not verify GitHub token. Please retry.",
            )
        return {"login": "unverified", "id": -1}


# ── Branch → Jira story inference ─────────────────────────────────────────────

async def _get_jira_context_for_branch(branch: str) -> str:
    """
    Fast-path: infer Jira story from branch name pattern
    feature/PROJ-123-some-description or fix/PROJ-456.

    Returns a formatted story context string or empty string if not found.
    The orchestrator's JiraContextPlugin can still call get_story for other
    stories the user mentions mid-conversation.
    """
    match = BRANCH_STORY_RE.search(branch)
    if not match:
        return ""
    story_key = match.group(1).upper()
    try:
        story = await _get_jira().get_story(story_key)
        logger.debug("Inferred Jira story from branch", branch=branch, story_key=story_key)
        return story.to_context_string()
    except Exception as exc:
        logger.debug(
            "Could not fetch story for branch",
            branch=branch,
            story_key=story_key,
            error=str(exc),
        )
        return ""


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/copilot")
async def copilot_chat(
    request: Request,
    body: CopilotRequest,
    x_github_token: str = Header(
        default="",
        alias="X-GitHub-Token",
        description="Short-lived token issued by GitHub Copilot.",
    ),
):
    """
    Copilot Extensions agent endpoint.

    GitHub validates the user's Copilot subscription before forwarding
    to this URL. We additionally verify the X-GitHub-Token so we only
    respond to requests that genuinely came from GitHub.
    """
    if not x_github_token:
        raise HTTPException(
            status_code=401,
            detail="X-GitHub-Token header is required.",
        )

    github_user = await _verify_github_token(x_github_token)

    ctx = body.context
    jira_context = await _get_jira_context_for_branch(ctx.branch)

    system_prompt = build_copilot_prompt(
        context=ctx.model_dump(),       # Pydantic v2 — not ctx.dict()
        jira_context=jira_context,
    )

    session_id = f"copilot_{ctx.repoName}_{ctx.branch}_{github_user.get('id', 0)}"
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())

    logger.info(
        "Copilot request received",
        github_user=github_user.get("login"),
        repo=ctx.repoName,
        branch=ctx.branch,
        active_file=ctx.activeFile or "none",
        has_jira_context=bool(jira_context),
    )

    async def event_generator():
        orchestrator = _get_orchestrator()

        async for chunk in orchestrator.stream_response(
            messages=body.messages,
            system_prompt=system_prompt,
            session_id=session_id,
        ):
            is_done = chunk.get("done", False)
            delta_text = chunk.get("delta", "")

            # Skip the internal done sentinel (empty delta, done=True) —
            # it carries citations for the UI but has no content to stream.
            if is_done and not delta_text:
                break

            # OpenAI-compatible streaming format required by Copilot spec
            payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": body.model,
                "choices": [{
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": delta_text,
                    },
                    "finish_reason": "stop" if is_done else None,
                }],
            }
            yield {"data": json.dumps(payload)}

        # Copilot spec requires [DONE] terminator
        yield {"data": "[DONE]"}

    from sse_starlette.sse import EventSourceResponse
    return EventSourceResponse(event_generator())


@router.get("/copilot/manifest")
async def copilot_manifest():
    """
    Returns the Copilot extension capability manifest.
    Register this URL as the 'Manifest URL' in your GitHub App settings.
    """
    return {
        "type": "agent",
        "name": "enterprise-ai-agent",
        "description": (
            "Jira-aware AI coding assistant. Injects story context from your "
            "current branch, searches the codebase, and queries project docs."
        ),
        "api": {"type": "openai"},
        "auth": {"type": "none"},
        "capabilities": {
            "tool_calling": True,
            "streaming": True,
        },
    }
