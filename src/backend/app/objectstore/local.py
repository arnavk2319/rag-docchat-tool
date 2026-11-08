"""Local-filesystem ObjectStore (dev and tests). Blocking file I/O runs in a worker thread."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

from .base import ObjectStoreError, validate_key


class LocalObjectStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, tenant_id: str, key: str) -> Path:
        p = self._root.joinpath(*validate_key(tenant_id, key)).resolve()
        if not p.is_relative_to(self._root):  # defence in depth (symlinks)
            raise ObjectStoreError("key escapes store root")
        return p

    async def put(self, tenant_id: str, key: str, data: bytes) -> None:
        p = self._path(tenant_id, key)

        def _w() -> None:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + f".tmp{os.getpid()}")
            tmp.write_bytes(data)
            tmp.replace(p)

        await asyncio.to_thread(_w)

    async def get(self, tenant_id: str, key: str) -> bytes | None:
        p = self._path(tenant_id, key)

        def _r() -> bytes | None:
            try:
                return p.read_bytes()
            except (FileNotFoundError, IsADirectoryError):
                return None

        return await asyncio.to_thread(_r)

    async def delete(self, tenant_id: str, key: str) -> None:
        p = self._path(tenant_id, key)

        def _d() -> None:
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
            with contextlib.suppress(OSError):  # drop the empty doc dir
                p.parent.rmdir()

        await asyncio.to_thread(_d)

    async def exists(self, tenant_id: str, key: str) -> bool:
        p = self._path(tenant_id, key)
        return await asyncio.to_thread(p.is_file)
