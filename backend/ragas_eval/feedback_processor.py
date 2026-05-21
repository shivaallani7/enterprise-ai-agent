"""
Weekly job: reads low-rated responses from Cosmos DB, formats them as RAGAS
test cases, and adds them to the golden dataset.
Run via: python -m ragas_eval.feedback_processor
"""
from __future__ import annotations

import asyncio
import time
import uuid

import structlog

logger = structlog.get_logger()


async def process_low_rated_feedback(days_back: int = 7) -> int:
    from app.memory.cosmos_store import FeedbackStore

    store = FeedbackStore()
    since_ts = int(time.time()) - days_back * 86400
    items = await store.get_low_rated(since_ts=since_ts, limit=200)

    if not items:
        logger.info("No low-rated feedback to process")
        return 0

    # Only process items with a correction (SME provided the right answer)
    actionable = [i for i in items if i.get("correction")]
    logger.info("Processing actionable feedback", count=len(actionable))

    from azure.cosmos.aio import CosmosClient
    import os

    endpoint = os.environ["COSMOS_ENDPOINT"]
    key = os.environ["COSMOS_KEY"]
    database = os.environ.get("COSMOS_DATABASE", "agent-db")

    saved = 0
    async with CosmosClient(url=endpoint, credential=key) as client:
        db = client.get_database_client(database)
        container = db.get_container_client("feedback")

        for item in actionable:
            # Promote to golden dataset entry
            golden_doc = {
                "id": str(uuid.uuid4()),
                "sessionId": f"golden_{item['sessionId']}",
                "messageId": str(uuid.uuid4()),
                "originalQuestion": item.get("originalQuestion", ""),
                "correction": item["correction"],
                "storyId": item.get("storyId"),
                "rating": 1,
                "synthetic": False,
                "promotedFrom": item["id"],
                "timestamp": int(time.time()),
            }
            await container.upsert_item(golden_doc)
            saved += 1

    logger.info("Promoted feedback to golden dataset", saved=saved)
    return saved


async def main():
    logger.info("Starting feedback processor")
    saved = await process_low_rated_feedback(days_back=7)
    logger.info("Feedback processor complete", total_saved=saved)


if __name__ == "__main__":
    asyncio.run(main())
