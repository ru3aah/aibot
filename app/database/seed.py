import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Source

logger = logging.getLogger(__name__)


def _load_seed_sources() -> List[Dict[str, Any]]:
    seed_path = Path(__file__).with_name("seed_sources.json")
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("seed_sources.json must be a JSON array")
    return data


async def seed_sources_if_empty(session: AsyncSession) -> int:
    """
    Insert sources from seed_sources.json only if sources table is empty.
    Returns number of inserted rows.
    """
    total = await session.scalar(select(func.count()).select_from(Source))
    if (total or 0) > 0:
        logger.info("seed_sources: skipped (sources already exist: %s)",
                    total)
        return 0

    rows = _load_seed_sources()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    inserted = 0
    for r in rows:
        src = Source(
            id=str(r["id"]),
            type=r["type"],
            name=str(r["name"]),
            url=str(r.get("url") or ""),
            enabled=bool(r.get("enabled", True)),
            created_at=now,
        )
        session.add(src)
        inserted += 1

    await session.commit()
    logger.info("seed_sources: inserted=%s", inserted)
    return inserted