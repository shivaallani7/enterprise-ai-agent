"""
Cosmos DB session, feedback, and user store.
Containers: "sessions" (chat history), "feedback" (SME ratings), "users" (profiles).
"""
from __future__ import annotations

import time
from typing import Any

import structlog
from azure.cosmos.aio import CosmosClient
from azure.cosmos import PartitionKey

from app.config import get_settings

logger = structlog.get_logger()
settings = get_settings()


class SessionStore:
    """
    Stores and retrieves conversation history.
    Partition key: userId
    Document id: {userId}_{sessionId}
    """

    def __init__(self):
        self._client: CosmosClient | None = None

    def _get_client(self) -> CosmosClient:
        if not self._client:
            self._client = CosmosClient(
                url=settings.cosmos_endpoint,
                credential=settings.cosmos_key,
            )
        return self._client

    async def _get_container(self):
        client = self._get_client()
        db = client.get_database_client(settings.cosmos_database)
        return db.get_container_client(settings.cosmos_sessions_container)

    async def get_history(self, user_id: str, session_id: str) -> list[dict]:
        container = await self._get_container()
        doc_id = f"{user_id}_{session_id}"
        try:
            item = await container.read_item(item=doc_id, partition_key=user_id)
            return item.get("messages", [])
        except Exception:
            return []

    async def save_message(
        self,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        container = await self._get_container()
        doc_id = f"{user_id}_{session_id}"

        # Upsert pattern: read → append → write
        try:
            item = await container.read_item(item=doc_id, partition_key=user_id)
        except Exception:
            item = {
                "id": doc_id,
                "userId": user_id,
                "sessionId": session_id,
                "messages": [],
                "createdAt": int(time.time()),
            }

        item["messages"].append({
            "role": role,
            "content": content,
            "timestamp": int(time.time()),
            **(metadata or {}),
        })
        item["updatedAt"] = int(time.time())

        await container.upsert_item(item)

    async def clear_session(self, user_id: str, session_id: str) -> None:
        container = await self._get_container()
        doc_id = f"{user_id}_{session_id}"
        try:
            await container.delete_item(item=doc_id, partition_key=user_id)
        except Exception:
            pass  # Already gone


class FeedbackStore:
    """
    Stores SME feedback (thumbs + correction text).
    Partition key: sessionId
    """

    def __init__(self):
        self._client: CosmosClient | None = None

    def _get_client(self) -> CosmosClient:
        if not self._client:
            self._client = CosmosClient(
                url=settings.cosmos_endpoint,
                credential=settings.cosmos_key,
            )
        return self._client

    async def _get_container(self):
        client = self._get_client()
        db = client.get_database_client(settings.cosmos_database)
        return db.get_container_client(settings.cosmos_feedback_container)

    async def save_feedback(
        self,
        session_id: str,
        message_id: str,
        rating: int,  # 1 = thumbs up, -1 = thumbs down
        correction: str | None,
        story_id: str | None,
        user_id: str,
    ) -> str:
        container = await self._get_container()
        import uuid
        doc_id = str(uuid.uuid4())
        item = {
            "id": doc_id,
            "sessionId": session_id,
            "messageId": message_id,
            "rating": rating,
            "correction": correction,
            "storyId": story_id,
            "userId": user_id,
            "timestamp": int(time.time()),
        }
        await container.upsert_item(item)
        return doc_id

    async def get_low_rated(self, since_ts: int, limit: int = 100) -> list[dict]:
        container = await self._get_container()
        query = (
            "SELECT * FROM c WHERE c.rating = -1 AND c.timestamp >= @since "
            "ORDER BY c.timestamp DESC OFFSET 0 LIMIT @limit"
        )
        items = []
        async for item in container.query_items(
            query=query,
            parameters=[
                {"name": "@since", "value": since_ts},
                {"name": "@limit", "value": limit},
            ],
        ):
            items.append(item)
        return items

    async def get_score_trends(self, days: int = 30) -> list[dict]:
        """Return daily aggregated scores for the dashboard."""
        container = await self._get_container()
        since_ts = int(time.time()) - days * 86400
        query = (
            "SELECT c.timestamp, c.rating FROM c "
            "WHERE c.timestamp >= @since ORDER BY c.timestamp ASC"
        )
        rows = []
        async for item in container.query_items(
            query=query,
            parameters=[{"name": "@since", "value": since_ts}],
        ):
            rows.append(item)
        return rows


class UserStore:
    """
    Stores user profiles (persona, display name, etc.).
    Partition key: id (same as sub)
    Auto-creates a profile on first access.
    """

    def __init__(self):
        self._client: CosmosClient | None = None

    def _get_client(self) -> CosmosClient:
        if not self._client:
            self._client = CosmosClient(
                url=settings.cosmos_endpoint,
                credential=settings.cosmos_key,
            )
        return self._client

    async def _get_container(self):
        client = self._get_client()
        db = client.get_database_client(settings.cosmos_database)
        # Create container if it doesn't exist yet
        try:
            return db.get_container_client(settings.cosmos_users_container)
        except Exception:
            await db.create_container_if_not_exists(
                id=settings.cosmos_users_container,
                partition_key=PartitionKey(path="/id"),
            )
            return db.get_container_client(settings.cosmos_users_container)

    async def get_or_create(
        self,
        sub: str,
        email: str,
        name: str,
        default_persona: str = "general",
    ) -> dict:
        """Return existing profile or create one with the given default persona."""
        container = await self._get_container()
        try:
            item = await container.read_item(item=sub, partition_key=sub)
            return item
        except Exception:
            now = int(time.time())
            item = {
                "id": sub,
                "sub": sub,
                "email": email,
                "name": name,
                "persona": default_persona,
                "created_at": now,
                "updated_at": now,
            }
            await container.upsert_item(item)
            logger.info("User profile created", sub=sub, email=email, persona=default_persona)
            return item

    async def update(self, sub: str, updates: dict) -> dict:
        """Merge updates into the user profile and return the updated doc."""
        container = await self._get_container()
        try:
            item = await container.read_item(item=sub, partition_key=sub)
        except Exception:
            raise ValueError(f"User {sub} not found")
        item.update(updates)
        item["updated_at"] = int(time.time())
        await container.upsert_item(item)
        return item

    async def get_sessions(self, sub: str, session_store: "SessionStore") -> list[dict]:  # noqa: F821
        """Return session summaries (id + first message + timestamp) for a user."""
        container = await session_store._get_container()
        query = (
            "SELECT c.sessionId, c.createdAt, c.updatedAt, c.messages FROM c "
            "WHERE c.userId = @uid ORDER BY c.updatedAt DESC OFFSET 0 LIMIT 50"
        )
        sessions = []
        async for item in container.query_items(
            query=query,
            parameters=[{"name": "@uid", "value": sub}],
        ):
            msgs = item.get("messages", [])
            first_user = next((m["content"] for m in msgs if m.get("role") == "user"), "")
            sessions.append({
                "sessionId": item.get("sessionId", ""),
                "createdAt": item.get("createdAt", 0),
                "updatedAt": item.get("updatedAt", 0),
                "preview": first_user[:80] + ("…" if len(first_user) > 80 else ""),
                "messageCount": len(msgs),
            })
        return sessions


class IngestRegistry:
    """
    Tracks ingested documents for INSERT vs UPDATE detection.
    Partition key: id (= filename — the stable lookup key)
    Container: ingest_registry
    """

    def __init__(self):
        self._client: CosmosClient | None = None

    def _get_client(self) -> CosmosClient:
        if not self._client:
            self._client = CosmosClient(
                url=settings.cosmos_endpoint,
                credential=settings.cosmos_key,
            )
        return self._client

    async def _get_container(self):
        client = self._get_client()
        db = client.get_database_client(settings.cosmos_database)
        try:
            await db.create_container_if_not_exists(
                id=settings.cosmos_ingest_container,
                partition_key=PartitionKey(path="/id"),
            )
        except Exception:
            pass
        return db.get_container_client(settings.cosmos_ingest_container)

    async def lookup(self, filename: str) -> dict | None:
        """Return existing registry record for this filename, or None."""
        container = await self._get_container()
        try:
            return await container.read_item(item=filename, partition_key=filename)
        except Exception:
            return None

    async def upsert(self, record: dict) -> None:
        """Save or update a registry record. record["id"] must equal filename."""
        container = await self._get_container()
        record["updated_at"] = int(time.time())
        await container.upsert_item(record)

    async def list_all(self) -> list[dict]:
        """List all ingested documents, most recently updated first."""
        container = await self._get_container()
        query = (
            "SELECT c.id, c.doc_id, c.file_name, c.doc_type, c.chunk_count, "
            "c.version, c.status, c.indexed_at, c.updated_at FROM c "
            "ORDER BY c.updated_at DESC OFFSET 0 LIMIT 100"
        )
        items = []
        async for item in container.query_items(query=query):
            items.append(item)
        return items

    async def delete(self, filename: str) -> None:
        """Remove a registry record (call after deleting its vectors)."""
        container = await self._get_container()
        try:
            await container.delete_item(item=filename, partition_key=filename)
        except Exception:
            pass
