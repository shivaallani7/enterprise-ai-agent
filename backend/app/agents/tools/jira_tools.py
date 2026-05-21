"""Langchain tools wrapping the existing JiraClient."""
from __future__ import annotations

import asyncio
from langchain_core.tools import tool
from app.integrations.jira_client import JiraClient

_client = JiraClient()


@tool
async def get_jira_story(story_key: str) -> str:
    """Fetch full details of a Jira story: title, description, acceptance criteria,
    status, assignee, and linked PRs. Use when the user asks about a specific story."""
    try:
        story = await _client.get_story(story_key)
        return story.to_context_string()
    except Exception as exc:
        return f"Could not fetch story {story_key}: {exc}"


@tool
async def get_jira_comments(story_key: str) -> str:
    """Fetch the latest comments on a Jira story. Use when the user asks about
    discussion, decisions, or clarifications on a story."""
    try:
        comments = await _client.get_comments(story_key)
        if not comments:
            return f"No comments on {story_key}."
        lines = [f"[{c['author']} @ {c['created'][:10]}]: {c['body']}" for c in comments]
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not fetch comments for {story_key}: {exc}"


@tool
async def get_linked_prs(story_key: str) -> str:
    """Get GitHub pull requests linked to a Jira story. Use when the user asks
    about related code changes or implementation status."""
    try:
        prs = await _client.get_linked_prs(story_key)
        if not prs:
            return f"No linked PRs for {story_key}."
        return "\n".join(
            f"PR #{p['number']}: {p['title']} ({p['state']}) — {p['url']}" for p in prs
        )
    except Exception as exc:
        return f"Could not fetch PRs for {story_key}: {exc}"


JIRA_TOOLS = [get_jira_story, get_jira_comments, get_linked_prs]
