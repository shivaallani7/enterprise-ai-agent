"""
One-time setup: create both Azure AI Search indexes with the correct schema.

Run this once after provisioning (azd up) and again if you change the schema.
Safe to re-run — uses create-or-update semantics.

Usage:
    python -m scripts.setup_indexes
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from app.integrations.search_client import IndexManager

logger = structlog.get_logger()


async def main() -> None:
    logger.info("Setting up Azure AI Search indexes")
    manager = IndexManager()
    await manager.create_or_update_indexes()
    logger.info("Index setup complete")


if __name__ == "__main__":
    asyncio.run(main())
