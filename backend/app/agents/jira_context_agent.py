"""
JiraContextPlugin — SK plugin that exposes Jira data as typed tool functions.

Parameter descriptions use Annotated[type, "description"] so SK generates
a rich OpenAI function schema. The LLM sees each parameter's description
when deciding which tool to call and how to fill its arguments.
"""
from __future__ import annotations

from typing import Annotated

import structlog
from semantic_kernel.functions import kernel_function

from app.integrations.jira_client import JiraClient

logger = structlog.get_logger()


class JiraContextPlugin:
    """Semantic Kernel plugin for Jira story context."""

    def __init__(self) -> None:
        self._client = JiraClient()

    @kernel_function(
        name="get_story",
        description=(
            "Fetch full details of a Jira story including title, description, "
            "acceptance criteria, status, and assignee. Use this when the user "
            "asks about a specific story or when you need story context."
        ),
    )
    async def get_story(
        self,
        story_key: Annotated[
            str,
            "The Jira story key, e.g. 'PROJ-123'. Must include the project prefix.",
        ],
    ) -> str:
        try:
            story = await self._client.get_story(story_key)
            return story.to_context_string()
        except Exception as exc:
            logger.warning("get_story failed", story_key=story_key, error=str(exc))
            return f"Could not fetch story {story_key}: {exc}"

    @kernel_function(
        name="get_comments",
        description=(
            "Fetch the latest comments on a Jira story. Useful when the user "
            "asks about discussion, decisions, or clarifications on a story. "
            "Returns author, date, and body for up to 20 recent comments."
        ),
    )
    async def get_comments(
        self,
        story_key: Annotated[
            str,
            "The Jira story key, e.g. 'PROJ-123'.",
        ],
    ) -> str:
        try:
            comments = await self._client.get_comments(story_key)
            if not comments:
                return f"No comments found for {story_key}."
            lines = [
                f"[{c['author']} @ {c['created'][:10]}]: {c['body']}"
                for c in comments[:20]
            ]
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("get_comments failed", story_key=story_key, error=str(exc))
            return f"Could not fetch comments for {story_key}: {exc}"

    @kernel_function(
        name="get_linked_prs",
        description=(
            "Get GitHub pull requests linked to a Jira story. Use this when "
            "the user asks about related code changes, PRs, or implementation status."
        ),
    )
    async def get_linked_prs(
        self,
        story_key: Annotated[
            str,
            "The Jira story key, e.g. 'PROJ-123'.",
        ],
    ) -> str:
        try:
            prs = await self._client.get_linked_prs(story_key)
            if not prs:
                return f"No linked PRs found for {story_key}."
            lines = [
                f"PR #{p['number']}: {p['title']} ({p['state']}) — {p['url']}"
                for p in prs
            ]
            return "\n".join(lines)
        except Exception as exc:
            logger.warning("get_linked_prs failed", story_key=story_key, error=str(exc))
            return f"Could not fetch PRs for {story_key}: {exc}"
