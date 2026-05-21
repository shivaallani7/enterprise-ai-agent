"""
Jira REST API v3 async client.

Key design decisions
────────────────────
ADF extraction
  Atlassian Document Format is a nested JSON tree. We walk it recursively,
  preserving semantic structure (headings, lists, code blocks) as plain text
  so the LLM receives readable content rather than raw JSON.

Parallel fetching
  `get_story` fetches the issue fields, comments, and remote links in one
  asyncio.gather call (three concurrent HTTP requests → ~3× faster than
  serial).

TTL cache
  Story details are cached for JIRA_STORY_CACHE_TTL seconds (default 300,
  matching the frontend 5-minute poll interval). This means chat requests
  within a tab don't re-fetch from Jira on every message — the story
  context is consistent within each poll window and Jira API quota is
  not consumed per-message.

JQL quoting
  `currentUser()` is a JQL function and must be passed bare.
  Real account IDs (UUIDs) must be quoted: assignee = "5dd1f3..."
  We detect the difference by checking for the trailing `()`.
"""
from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


# ── TTL cache ──────────────────────────────────────────────────────────────────

class _TTLCache:
    """Minimal in-process TTL cache. Thread-safe for asyncio (single-threaded)."""

    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, time.monotonic() + self._ttl)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)


# ── ADF extraction ─────────────────────────────────────────────────────────────

def _extract_adf_text(node: Any, _list_index: int = 0) -> str:
    """
    Recursively convert an Atlassian Document Format node tree to plain text.

    Handles: doc, paragraph, heading, text (with marks), hardBreak,
             bulletList, orderedList, listItem, codeBlock, code (inline),
             blockquote, rule, mention, emoji, inlineCard, table family.

    Intentionally ignores: media, mediaGroup, mediaSingle (images/attachments),
             unknown node types (returns empty string).
    """
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_extract_adf_text(n) for n in node)
    if not isinstance(node, dict):
        return ""

    t = node.get("type", "")
    attrs = node.get("attrs", {})
    content = node.get("content") or []

    # ── Leaf nodes ─────────────────────────────────────────────────────────────
    if t == "text":
        text = node.get("text", "")
        # Apply link mark if present — surface the URL so the LLM can cite it
        for mark in node.get("marks") or []:
            if mark.get("type") == "link":
                href = (mark.get("attrs") or {}).get("href", "")
                if href:
                    text = f"{text} ({href})"
        return text

    if t == "hardBreak":
        return "\n"

    if t == "rule":
        return "\n---\n"

    if t == "mention":
        # attrs.text is the display name e.g. "@Jane Smith"
        return attrs.get("text", attrs.get("id", "@mention"))

    if t == "emoji":
        return attrs.get("text", attrs.get("shortName", ""))

    if t == "inlineCard":
        # A Jira/Confluence smart link — surface the URL
        return attrs.get("url", "")

    # ── Block nodes ────────────────────────────────────────────────────────────
    if t == "doc":
        return _extract_adf_text(content)

    if t == "paragraph":
        inner = _extract_adf_text(content)
        return inner + "\n" if inner else "\n"

    if t == "heading":
        level = attrs.get("level", 1)
        prefix = "#" * level + " "
        return prefix + _extract_adf_text(content) + "\n"

    if t == "codeBlock":
        lang = attrs.get("language", "")
        fence = f"```{lang}\n" if lang else "```\n"
        return fence + _extract_adf_text(content) + "\n```\n"

    if t == "code":
        # Inline code mark — shouldn't appear as a top-level node but handle it
        return "`" + node.get("text", "") + "`"

    if t == "blockquote":
        # Prefix each line with >
        inner = _extract_adf_text(content)
        lines = inner.splitlines(keepends=True)
        return "".join("> " + line for line in lines) + "\n"

    if t == "bulletList":
        items = []
        for item in content:
            items.append("• " + _extract_adf_text(item).strip())
        return "\n".join(items) + "\n"

    if t == "orderedList":
        items = []
        for idx, item in enumerate(content, start=attrs.get("order", 1)):
            items.append(f"{idx}. " + _extract_adf_text(item).strip())
        return "\n".join(items) + "\n"

    if t == "listItem":
        return _extract_adf_text(content)

    # ── Table (flatten to readable text) ──────────────────────────────────────
    if t == "table":
        return _extract_adf_text(content) + "\n"

    if t in ("tableRow",):
        cells = [_extract_adf_text(c).strip() for c in content]
        return " | ".join(cells) + "\n"

    if t in ("tableCell", "tableHeader"):
        return _extract_adf_text(content)

    # ── Unknown / ignored node types ──────────────────────────────────────────
    # Pass through content children so we don't silently drop nested text
    if content:
        return _extract_adf_text(content)

    return ""


def _adf_to_text(raw: Any) -> str:
    """Public wrapper — strips leading/trailing whitespace from extracted text."""
    return _extract_adf_text(raw).strip()


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class JiraStory:
    key: str
    title: str
    description: str
    acceptance_criteria: str
    status: str
    assignee: str
    story_points: str = ""
    pr_list: str = ""
    comments: str = ""
    raw: dict = field(default_factory=dict)

    def to_context_string(self) -> str:
        parts = [
            f"Story: {self.key} — {self.title}",
            f"Status: {self.status} | Assignee: {self.assignee}"
            + (f" | Points: {self.story_points}" if self.story_points else ""),
            "",
            "Description:",
            self.description or "No description provided.",
            "",
            "Acceptance Criteria:",
            self.acceptance_criteria or "None specified.",
            "",
            f"Linked PRs: {self.pr_list or 'None.'}",
            "",
            "Recent Comments:",
            self.comments or "None.",
        ]
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": self.acceptance_criteria,
            "status": self.status,
            "assignee": self.assignee,
            "story_points": self.story_points,
            "pr_list": self.pr_list,
            "comments": self.comments,
        }


# ── Auth helpers ───────────────────────────────────────────────────────────────

def _auth_header() -> dict[str, str]:
    token = base64.b64encode(
        f"{settings.jira_user_email}:{settings.jira_api_token}".encode()
    ).decode()
    return {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _jql_assignee(account_id: str) -> str:
    """
    Return the correct JQL assignee clause.

    `currentUser()` is a JQL function — pass it bare.
    All other values are literal account IDs that must be quoted.
    """
    if account_id.endswith("()"):
        return f"assignee = {account_id}"
    return f'assignee = "{account_id}"'


# ── Client ─────────────────────────────────────────────────────────────────────

class JiraClient:
    """Async Jira REST API v3 client with TTL caching."""

    def __init__(self) -> None:
        self._base_url = settings.jira_base_url.rstrip("/")
        self._project_key = settings.jira_project_key
        self._ac_field = settings.jira_ac_custom_field
        self._headers = _auth_header()
        self._cache: _TTLCache = _TTLCache(ttl=settings.jira_story_cache_ttl)

    # ── Public API ────────────────────────────────────────────────────────────

    async def get_my_open_stories(self, account_id: str) -> list[JiraStory]:
        """
        Return open stories in the configured project assigned to `account_id`,
        ordered by most recently updated.

        Uses POST /rest/api/3/search/jql (GET /search deprecated 2024).
        When using Basic auth the assignee filter falls back to all project
        stories (currentUser() requires OAuth context).
        """
        # Build assignee clause — omit if using Basic auth dev token
        assignee_clause = (
            f"AND {_jql_assignee(account_id)} "
            if not account_id.endswith("()") and account_id != "dev-user"
            else ""
        )
        jql = (
            f"project = {self._project_key} "
            f"{assignee_clause}"
            f"AND status NOT IN (Done, Closed, Resolved) "
            f"ORDER BY updated DESC"
        )
        fields = ["summary", "description", "status", "assignee", self._ac_field]

        async with self._client() as c:
            resp = await c.post(
                f"{self._base_url}/rest/api/3/search/jql",
                json={"jql": jql, "maxResults": 50, "fields": fields},
            )
            self._raise_for_status(resp, context="list stories")
            data = resp.json()

        return [self._parse_issue(issue) for issue in data.get("issues", [])]

    async def get_story(self, story_key: str) -> JiraStory:
        """
        Fetch full story detail (fields + comments + linked PRs).

        Results are cached for `jira_story_cache_ttl` seconds to avoid
        re-fetching on every chat message within a session.
        """
        cached = self._cache.get(story_key)
        if cached is not None:
            logger.debug("Jira story cache hit", story_key=story_key)
            return cached

        story = await self._fetch_story_uncached(story_key)
        self._cache.set(story_key, story)
        return story

    def invalidate_cache(self, story_key: str) -> None:
        """Force-expire a cached story (e.g. after a webhook notification)."""
        self._cache.invalidate(story_key)

    async def get_comments(self, story_key: str) -> list[dict]:
        """Return up to 20 most recent comments for a story."""
        async with self._client() as c:
            resp = await c.get(
                f"{self._base_url}/rest/api/3/issue/{story_key}/comment",
                params={"maxResults": 20, "orderBy": "-created"},
            )
            self._raise_for_status(resp, context=f"get comments for {story_key}")
            data = resp.json()

        return [
            {
                "author": (c.get("author") or {}).get("displayName", "Unknown"),
                "created": c.get("created", ""),
                "body": _adf_to_text(c.get("body")),
            }
            for c in data.get("comments", [])
        ]

    async def get_linked_prs(self, story_key: str) -> list[dict]:
        """
        Return GitHub PRs linked to the story.

        Tries two sources:
        1. Remote links (POST /remotelink) — used by GitHub for Jira app
        2. issuelinks — covers manual links and some integrations

        Returns an empty list (not an error) if the Jira instance has no
        remote links configured (404 from the endpoint).
        """
        prs: list[dict] = []

        async with self._client() as c:
            # Source 1: remote links (GitHub for Jira app)
            resp = await c.get(
                f"{self._base_url}/rest/api/3/issue/{story_key}/remotelink",
            )
            if resp.status_code == 200:
                for link in resp.json():
                    obj = link.get("object", {})
                    url = obj.get("url", "")
                    if "pull" in url or "/pr/" in url.lower():
                        prs.append({
                            "title": obj.get("title", url),
                            "url": url,
                            "number": _extract_pr_number(url),
                            "state": (obj.get("status") or {}).get("description", "unknown"),
                            "source": "remote_link",
                        })
            elif resp.status_code not in (403, 404):
                logger.warning(
                    "Unexpected status from remotelink",
                    story_key=story_key,
                    status=resp.status_code,
                )

        return prs

    # ── Private helpers ───────────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0),
        )

    async def _fetch_story_uncached(self, story_key: str) -> JiraStory:
        """Fetch all story data with parallel HTTP requests."""
        fields = (
            f"summary,description,status,assignee,comment,issuelinks,"
            f"priority,labels,fixVersions,{self._ac_field}"
        )

        # Parallel: fetch issue fields + comments + remote links simultaneously
        async with self._client() as c:
            issue_task = c.get(
                f"{self._base_url}/rest/api/3/issue/{story_key}",
                params={"fields": fields},
            )
            comment_task = c.get(
                f"{self._base_url}/rest/api/3/issue/{story_key}/comment",
                params={"maxResults": 20, "orderBy": "-created"},
            )
            remotelink_task = c.get(
                f"{self._base_url}/rest/api/3/issue/{story_key}/remotelink",
            )
            issue_resp, comment_resp, remotelink_resp = await asyncio.gather(
                issue_task, comment_task, remotelink_task,
                return_exceptions=False,
            )

        # Issue fields
        self._raise_for_status(issue_resp, context=f"get story {story_key}")
        story = self._parse_issue(issue_resp.json())

        # Comments (non-fatal if this failed)
        if comment_resp.status_code == 200:
            comments_raw = comment_resp.json().get("comments", [])
            story.comments = self._format_comments_list(comments_raw)
        else:
            logger.warning(
                "Could not fetch comments",
                story_key=story_key,
                status=comment_resp.status_code,
            )

        # Remote links / PRs (non-fatal)
        if remotelink_resp.status_code == 200:
            story.pr_list = self._format_prs(remotelink_resp.json())
        elif remotelink_resp.status_code not in (403, 404):
            logger.warning(
                "Unexpected status from remotelink",
                story_key=story_key,
                status=remotelink_resp.status_code,
            )

        logger.info("Jira story fetched", story_key=story_key, status=story.status)
        return story

    def _parse_issue(self, issue: dict) -> JiraStory:
        fields = issue.get("fields", {})

        ac_raw = fields.get(self._ac_field)
        if isinstance(ac_raw, dict):
            acceptance_criteria = _adf_to_text(ac_raw)
        elif isinstance(ac_raw, (int, float)):
            # Field is Story Points on this instance — AC is in description
            acceptance_criteria = ""
        elif ac_raw:
            acceptance_criteria = str(ac_raw)
        else:
            acceptance_criteria = ""

        # Story points may be in the same field or a numeric field
        story_points = ""
        sp_raw = fields.get(self._ac_field)
        if isinstance(sp_raw, (int, float)):
            story_points = str(sp_raw)

        return JiraStory(
            key=issue["key"],
            title=fields.get("summary", ""),
            description=_adf_to_text(fields.get("description")),
            acceptance_criteria=acceptance_criteria,
            status=(fields.get("status") or {}).get("name", "Unknown"),
            assignee=(fields.get("assignee") or {}).get("displayName", "Unassigned"),
            story_points=story_points,
            raw=issue,
        )

    def _format_comments_list(self, comments: list[dict]) -> str:
        """Format up to 5 most recent comments for the system prompt."""
        if not comments:
            return "None."
        lines = []
        for c in comments[:5]:
            author = (c.get("author") or {}).get("displayName", "Unknown")
            created = c.get("created", "")[:10]
            body = _adf_to_text(c.get("body"))
            # Truncate very long comments so they don't dominate the prompt
            if len(body) > 500:
                body = body[:497] + "…"
            lines.append(f"[{author} @ {created}]: {body}")
        return "\n".join(lines)

    def _format_prs(self, remote_links: list[dict]) -> str:
        """Extract PR references from remote links."""
        prs = []
        for link in remote_links:
            obj = link.get("object", {})
            url = obj.get("url", "")
            if "pull" in url or "/pr/" in url.lower():
                number = _extract_pr_number(url)
                title = obj.get("title", url)
                state = (obj.get("status") or {}).get("description", "")
                label = f"#{number} {title}" if number else title
                if state:
                    label += f" ({state})"
                prs.append(label)
        return ", ".join(prs) if prs else "None."

    @staticmethod
    def _raise_for_status(resp: httpx.Response, context: str = "") -> None:
        """
        Raise with a readable message. Converts common status codes to
        user-facing descriptions rather than raw httpx exceptions.
        """
        if resp.status_code == 200:
            return
        if resp.status_code == 401:
            raise JiraAuthError(
                "Jira authentication failed. Check JIRA_API_TOKEN and JIRA_USER_EMAIL."
            )
        if resp.status_code == 403:
            raise JiraPermissionError(
                f"Permission denied on Jira {context}. "
                "The service account may not have access to this project."
            )
        if resp.status_code == 404:
            raise JiraNotFoundError(
                f"Jira resource not found: {context}. "
                "Check that the story key and project exist."
            )
        if resp.status_code == 429:
            raise JiraRateLimitError(
                "Jira API rate limit hit. Requests will resume automatically."
            )
        resp.raise_for_status()  # fallback for 5xx etc.


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_pr_number(url: str) -> str:
    """Extract PR number from GitHub pull URL. Returns '' if not found."""
    # https://github.com/org/repo/pull/123
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-2] in ("pull", "pr"):
        candidate = parts[-1]
        if candidate.isdigit():
            return candidate
    return ""


# ── Custom exceptions ──────────────────────────────────────────────────────────

class JiraError(Exception):
    """Base for Jira client errors."""

class JiraAuthError(JiraError):
    """401 from Jira."""

class JiraPermissionError(JiraError):
    """403 from Jira."""

class JiraNotFoundError(JiraError):
    """404 from Jira."""

class JiraRateLimitError(JiraError):
    """429 from Jira."""
