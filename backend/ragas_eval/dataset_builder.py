"""
Generates synthetic test questions from Jira stories using GPT-4o,
then saves them as golden Q&A pairs to the Cosmos DB feedback container.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path

import structlog
from openai import AzureOpenAI

logger = structlog.get_logger()


def _openai_client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-05-01-preview"),
    )


def generate_questions_for_story(story: dict, n: int = 5) -> list[dict]:
    """
    Call GPT-4o to generate n test question/answer pairs for a story.
    Returns list of { question, expected_answer, story_key }.
    """
    client = _openai_client()
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    prompt = f"""You are generating test cases for an AI agent that helps developers implement Jira stories.

Story: {story['key']} — {story['title']}
Description: {story.get('description', 'N/A')}
Acceptance Criteria: {story.get('acceptance_criteria', 'N/A')}

Generate {n} diverse test questions a developer might ask about this story, along with ideal answers.
Focus on: implementation approach, acceptance criteria clarification, related code patterns, edge cases.

Return a JSON array: [{{"question": "...", "expected_answer": "..."}}]
Only return valid JSON, no commentary."""

    resp = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        response_format={"type": "json_object"},
    )

    raw = resp.choices[0].message.content or "[]"
    try:
        data = json.loads(raw)
        pairs = data if isinstance(data, list) else data.get("pairs", data.get("questions", []))
        return [
            {
                "question": p["question"],
                "expected_answer": p["expected_answer"],
                "story_key": story["key"],
            }
            for p in pairs
        ]
    except Exception as exc:
        logger.warning("Failed to parse GPT response", error=str(exc), raw=raw[:200])
        return []


async def save_to_cosmos(pairs: list[dict]) -> int:
    from azure.cosmos.aio import CosmosClient

    endpoint = os.environ["COSMOS_ENDPOINT"]
    key = os.environ["COSMOS_KEY"]
    database = os.environ.get("COSMOS_DATABASE", "agent-db")

    saved = 0
    async with CosmosClient(url=endpoint, credential=key) as client:
        db = client.get_database_client(database)
        container = db.get_container_client("feedback")
        for pair in pairs:
            doc = {
                "id": str(uuid.uuid4()),
                "sessionId": f"synthetic_{pair['story_key']}",
                "messageId": str(uuid.uuid4()),
                "originalQuestion": pair["question"],
                "correction": pair["expected_answer"],
                "storyId": pair["story_key"],
                "rating": 1,
                "synthetic": True,
                "timestamp": int(__import__("time").time()),
            }
            await container.upsert_item(doc)
            saved += 1

    return saved


async def main():
    from app.integrations.jira_client import JiraClient

    jira = JiraClient()
    # Fetch recent stories from the project
    import httpx, base64
    from app.config import get_settings
    settings = get_settings()

    auth = base64.b64encode(
        f"{settings.jira_user_email}:{settings.jira_api_token}".encode()
    ).decode()
    async with httpx.AsyncClient() as client:
        ac_field = settings.jira_ac_custom_field or "customfield_10014"
        resp = await client.get(
            f"{settings.jira_base_url}/rest/api/3/search",
            headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
            params={
                "jql": f"project = {settings.jira_project_key} ORDER BY updated DESC",
                "maxResults": 20,
                "fields": f"summary,description,{ac_field}",
            },
        )
        resp.raise_for_status()
        issues = resp.json().get("issues", [])

    all_pairs = []
    for issue in issues:
        ac_field = settings.jira_ac_custom_field or "customfield_10014"
        story = {
            "key": issue["key"],
            "title": issue["fields"].get("summary", ""),
            "description": str(issue["fields"].get("description", "")),
            "acceptance_criteria": str(issue["fields"].get(ac_field, "")),
        }
        pairs = generate_questions_for_story(story, n=5)
        all_pairs.extend(pairs)
        logger.info("Generated questions", story=story["key"], count=len(pairs))

    saved = await save_to_cosmos(all_pairs)
    logger.info("Dataset build complete", total_saved=saved)


if __name__ == "__main__":
    asyncio.run(main())
