"""Purge expired anonymous data (T-036): documents (registry, vectors, parents, BM25, original) + sessions.

Scoped strictly to the demo tenant and owners starting with `anon-`; real tenants are never touched.
Bounded (`batch` docs per run), idempotent, tolerant of per-document failures. Logs counts and a short
hash of the doc id only: never content, filenames or owner ids.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.concurrency import run_blocking
from app.core.config import Settings, get_settings
from app.services.ingestion import DocumentBusyError

log = logging.getLogger("app.cleanup")

ANON_PREFIX = "anon-"


def _h(doc_id: str) -> str:
    return hashlib.sha256(doc_id.encode()).hexdigest()[:8]


async def purge_expired_anonymous(
    ingestion: Any,
    sessions: Any | None,
    *,
    tenant_id: str,
    ttl_hours: float,
    now: datetime | None = None,
    batch: int = 200,
) -> dict[str, int]:
    """Delete anonymous documents older than `ttl_hours` plus expired sessions of the demo tenant.

    `ingestion` is the app-wide IngestionService (bound here to `tenant_id`); returns counts.
    """
    now = now or datetime.now(UTC)
    ing = ingestion.for_tenant(tenant_id)
    cutoff = now - timedelta(hours=ttl_hours)
    docs = await run_blocking(ing.registry.list_older_than, ANON_PREFIX, cutoff, batch)
    deleted = failed = 0
    for d in docs:
        if d.tenant_id != tenant_id or not d.owner_id.startswith(ANON_PREFIX):
            continue  # defence in depth: never delete outside the anonymous scope
        try:
            if await ing.delete(d.owner_id, d.doc_id):
                deleted += 1
        except DocumentBusyError:
            pass  # mid-ingest; the next run picks it up
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.error("anon purge failed doc=%s err=%s", _h(d.doc_id), type(e).__name__)
    purged_sessions = 0
    if sessions is not None:
        try:
            purged_sessions = await run_blocking(
                sessions.for_tenant(tenant_id).purge_expired
            )
        except Exception as e:  # noqa: BLE001
            log.error("anon session purge failed err=%s", type(e).__name__)
    log.info(
        "anon purge docs=%d failed=%d sessions=%d", deleted, failed, purged_sessions
    )
    return {"documents": deleted, "failed": failed, "sessions": purged_sessions}


async def run_purge(
    app_state: Any, settings: Settings | None = None, now: datetime | None = None
) -> dict[str, int]:
    s = settings or get_settings()
    return await purge_expired_anonymous(
        app_state.ingestion,
        getattr(app_state, "sessions", None),
        tenant_id=s.demo_tenant_id,
        ttl_hours=s.anon_data_ttl_hours,
        now=now,
        batch=s.purge_batch_size,
    )


async def purge_loop(
    app_state: Any,
    interval_s: float,
    settings: Settings | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> None:
    """Run forever until cancelled; a failing pass never kills the loop."""
    while True:
        try:
            await run_purge(app_state, settings)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("anon purge pass failed err=%s", type(e).__name__)
        await sleep(interval_s)
