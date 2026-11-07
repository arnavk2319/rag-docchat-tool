"""ObjectStore interface (ADR-0003 section 4) and key scheme `tenant/<tenant>/<doc>/<name>`.

Every call takes tenant_id first and the key must live under that tenant's prefix, so a
bug elsewhere cannot cross tenants. Keys are validated against path traversal.
"""

from __future__ import annotations

import re
from typing import Protocol

_SEG = re.compile(r"^[A-Za-z0-9._@=+-]{1,200}$")
_NAME_BAD = re.compile(r"[^A-Za-z0-9._-]")


class ObjectStoreError(Exception):
    """Invalid key or backend failure (message never includes object content)."""


def _check_segment(s: str) -> str:
    if s in (".", "..") or not _SEG.match(s):
        raise ObjectStoreError("invalid key segment")
    return s


def original_key(tenant_id: str, doc_id: str, filename: str) -> str:
    """Key for an uploaded original; the filename is reduced to a safe single segment."""
    name = _NAME_BAD.sub("_", filename.replace("\\", "/").rsplit("/", 1)[-1]).lstrip(".") or "file"
    return f"tenant/{_check_segment(tenant_id)}/{_check_segment(doc_id)}/{name[:200]}"


def validate_key(tenant_id: str, key: str) -> list[str]:
    """Return the key's segments, or raise unless it is `tenant/<tenant_id>/...` and traversal-free."""
    parts = key.split("/")
    if len(parts) < 4 or parts[0] != "tenant" or parts[1] != _check_segment(tenant_id):
        raise ObjectStoreError("key outside tenant prefix")
    for p in parts:
        _check_segment(p)
    return parts


class ObjectStore(Protocol):
    async def put(self, tenant_id: str, key: str, data: bytes) -> None: ...
    async def get(self, tenant_id: str, key: str) -> bytes | None: ...
    async def delete(self, tenant_id: str, key: str) -> None: ...
    async def exists(self, tenant_id: str, key: str) -> bool: ...
