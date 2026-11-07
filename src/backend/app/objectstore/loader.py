"""Sync table_loader for the agent's query_table tool, backed by the ObjectStore."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..vectorstore.sync import DEFAULT_TENANT, run_sync
from .base import ObjectStore, original_key

log = logging.getLogger(__name__)


def make_table_loader(
    objects: ObjectStore | None, registry: Any, tenant_id: str = DEFAULT_TENANT
) -> Callable[[str, str], bytes | None] | None:
    """(owner_id, doc_id) -> original bytes, or None when unknown/foreign/missing/unavailable.

    The registry lookup is owner-scoped, so a foreign doc_id never reaches the store.
    Called from a worker thread (tools are sync).
    """
    if objects is None:
        return None

    def load(owner_id: str, doc_id: str) -> bytes | None:
        doc = registry.get(owner_id, doc_id)
        if doc is None:
            return None
        try:
            return run_sync(objects.get(tenant_id, original_key(tenant_id, doc.doc_id, doc.filename)))
        except Exception as e:  # noqa: BLE001
            log.error("table load failed doc=%s err=%s", doc_id, type(e).__name__)
            return None

    return load
