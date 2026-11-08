"""Tenant-scoped views over the owner-keyed local stores (parents SQLite, in-process BM25).

Those stores key rows by owner_id only. A view rewrites the owner key to `<tenant>:<owner>` so that
two tenants with the same owner id never share rows or index entries. `:` cannot occur in a valid
id (see core.auth.valid_id), so a scoped key cannot be forged. The legacy DEFAULT_TENANT maps to
the bare owner id (unchanged behaviour for single-tenant data and unit tests).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from app.vectorstore.base import DEFAULT_TENANT

if TYPE_CHECKING:
    from app.store.bm25 import Bm25Store
    from app.store.parents import Parent, SqliteParents


def scope_owner(tenant_id: str, owner_id: str) -> str:
    if not owner_id:
        raise ValueError("owner_id is required")
    return owner_id if tenant_id == DEFAULT_TENANT else f"{tenant_id}:{owner_id}"


class TenantParents:
    def __init__(self, inner: SqliteParents, tenant_id: str) -> None:
        self._i, self._t = inner, tenant_id

    def for_tenant(self, tenant_id: str) -> TenantParents:
        return TenantParents(self._i, tenant_id)

    def _o(self, owner_id: str) -> str:
        return scope_owner(self._t, owner_id)

    def replace_doc(self, owner_id: str, doc_id: str, parents: Sequence[Parent]) -> None:
        o = self._o(owner_id)
        self._i.replace_doc(o, doc_id, [p.model_copy(update={"owner_id": o}) for p in parents])

    def delete_doc(self, owner_id: str, doc_id: str) -> None:
        self._i.delete_doc(self._o(owner_id), doc_id)

    def get_parents(self, owner_id: str, parent_ids: Sequence[str]) -> list[Parent]:
        return [p.model_copy(update={"owner_id": owner_id})
                for p in self._i.get_parents(self._o(owner_id), parent_ids)]

    def list_doc_parents(self, owner_id: str, doc_id: str) -> list[Parent]:
        return [p.model_copy(update={"owner_id": owner_id})
                for p in self._i.list_doc_parents(self._o(owner_id), doc_id)]

    def count(self, owner_id: str, doc_id: str | None = None) -> int:
        return self._i.count(self._o(owner_id), doc_id)


class TenantBm25:
    def __init__(self, inner: Bm25Store, tenant_id: str) -> None:
        self._i, self._t = inner, tenant_id

    def for_tenant(self, tenant_id: str) -> TenantBm25:
        return TenantBm25(self._i, tenant_id)

    def _o(self, owner_id: str) -> str:
        return scope_owner(self._t, owner_id)

    def has_owner(self, owner_id: str) -> bool:
        return self._i.has_owner(self._o(owner_id))

    def load_owner(self, owner_id: str, docs: dict[str, tuple[list[str], list[str]]]) -> None:
        self._i.load_owner(self._o(owner_id), docs)

    def add_doc(self, owner_id: str, doc_id: str, chunk_ids: Sequence[str], texts: Sequence[str]) -> None:
        self._i.add_doc(self._o(owner_id), doc_id, chunk_ids, texts)

    def delete_doc(self, owner_id: str, doc_id: str) -> None:
        self._i.delete_doc(self._o(owner_id), doc_id)

    def has_doc(self, owner_id: str, doc_id: str) -> bool:
        return self._i.has_doc(self._o(owner_id), doc_id)

    def query(self, owner_id: str, text: str, k: int = 20, doc_ids: Sequence[str] | None = None):
        return self._i.query(self._o(owner_id), text, k, doc_ids)
