"""Postgres Registry and SessionStore (sync psycopg_pool.ConnectionPool; callers use run_blocking).

Bound to one tenant per instance; `for_tenant(tid)` returns a view for the authenticated tenant.
Every statement filters on tenant_id and owner_id and binds values as parameters (schema:
migrations/0001_init.sql). `pool` may be any object with the same `.connection()` surface.
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.llm import _approx_tokens
from app.store.registry import _STATUSES, Document, Status
from app.store.sessions import (
    FIRST_Q_CHARS,
    PURGE_EVERY_S,
    SessionLimitError,
    SessionMessage,
    SessionSummary,
    _clip,
    bound_messages,
)
from app.vectorstore import DEFAULT_TENANT

_DOC_COLS = (
    "doc_id, owner_id, filename, mime, size, sha256, pages_or_sheets, created_at, status, failure_reason"
)


def _doc(r: tuple, tenant_id: str = DEFAULT_TENANT) -> Document:
    return Document(
        tenant_id=tenant_id, doc_id=r[0], owner_id=r[1], filename=r[2], mime=r[3], size=r[4], sha256=r[5],
        pages_or_sheets=r[6], created_at=r[7], status=r[8], failure_reason=r[9],
    )


class PostgresRegistry:
    def __init__(self, pool: Any, tenant_id: str = DEFAULT_TENANT) -> None:
        self._pool, self._t = pool, tenant_id

    def for_tenant(self, tenant_id: str) -> PostgresRegistry:
        return PostgresRegistry(self._pool, tenant_id)

    def create(self, owner_id, filename, mime, size, sha256, pages_or_sheets=0, status="pending") -> str:
        return self.create_if_absent(
            owner_id, filename, mime, size, sha256, pages_or_sheets, status
        )[0].doc_id

    def create_if_absent(
        self, owner_id, filename, mime, size, sha256, pages_or_sheets=0, status="pending"
    ) -> tuple[Document, bool]:
        doc = Document(
            doc_id=uuid.uuid4().hex, owner_id=owner_id, filename=filename, mime=mime, size=size,
            sha256=sha256, pages_or_sheets=pages_or_sheets, created_at=datetime.now(UTC),
            status=status,
        )
        with self._pool.connection() as c:
            cur = c.execute(
                "INSERT INTO documents (tenant_id, owner_id, doc_id, filename, mime, size, sha256, "
                "pages_or_sheets, created_at, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id, owner_id, sha256) DO NOTHING",
                (self._t, owner_id, doc.doc_id, filename, mime, size, sha256, pages_or_sheets,
                 doc.created_at, status),
            )
            created = cur.rowcount == 1
            row = c.execute(
                f"SELECT {_DOC_COLS} FROM documents WHERE tenant_id=%s AND owner_id=%s AND sha256=%s",
                (self._t, owner_id, sha256),
            ).fetchone()
        return _doc(row, self._t), created

    def get_by_sha256(self, owner_id: str, sha256: str) -> Document | None:
        with self._pool.connection() as c:
            row = c.execute(
                f"SELECT {_DOC_COLS} FROM documents WHERE tenant_id=%s AND owner_id=%s AND sha256=%s",
                (self._t, owner_id, sha256),
            ).fetchone()
        return _doc(row, self._t) if row else None

    def get(self, owner_id: str, doc_id: str) -> Document | None:
        with self._pool.connection() as c:
            row = c.execute(
                f"SELECT {_DOC_COLS} FROM documents WHERE tenant_id=%s AND owner_id=%s AND doc_id=%s",
                (self._t, owner_id, doc_id),
            ).fetchone()
        return _doc(row, self._t) if row else None

    def list_by_owner(self, owner_id: str) -> list[Document]:
        with self._pool.connection() as c:
            rows = c.execute(
                f"SELECT {_DOC_COLS} FROM documents WHERE tenant_id=%s AND owner_id=%s "
                "ORDER BY created_at, doc_id",
                (self._t, owner_id),
            ).fetchall()
        return [_doc(r, self._t) for r in rows]

    def list_older_than(self, owner_prefix: str, before: datetime, limit: int) -> list[Document]:
        with self._pool.connection() as c:
            rows = c.execute(
                f"SELECT {_DOC_COLS} FROM documents WHERE tenant_id=%s AND left(owner_id, %s)=%s "
                "AND created_at < %s ORDER BY created_at, doc_id LIMIT %s",
                (self._t, len(owner_prefix), owner_prefix, before, limit),
            ).fetchall()
        return [_doc(r, self._t) for r in rows]

    def delete(self, owner_id: str, doc_id: str) -> bool:
        with self._pool.connection() as c:
            cur = c.execute(
                "DELETE FROM documents WHERE tenant_id=%s AND owner_id=%s AND doc_id=%s",
                (self._t, owner_id, doc_id),
            )
            return cur.rowcount > 0

    def update_status(self, owner_id: str, doc_id: str, status: Status) -> bool:
        if status not in _STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        with self._pool.connection() as c:
            cur = c.execute(
                "UPDATE documents SET status=%s, "
                "failure_reason=CASE WHEN %s='failed' THEN failure_reason ELSE NULL END "
                "WHERE tenant_id=%s AND owner_id=%s AND doc_id=%s",
                (status, status, self._t, owner_id, doc_id),
            )
            return cur.rowcount > 0

    def update_pages_or_sheets(self, owner_id: str, doc_id: str, n: int) -> bool:
        if n < 0:
            raise ValueError("pages_or_sheets must be >= 0")
        with self._pool.connection() as c:
            cur = c.execute(
                "UPDATE documents SET pages_or_sheets=%s WHERE tenant_id=%s AND owner_id=%s AND doc_id=%s",
                (n, self._t, owner_id, doc_id),
            )
            return cur.rowcount > 0

    def set_failure_reason(self, owner_id: str, doc_id: str, reason: str | None) -> bool:
        with self._pool.connection() as c:
            cur = c.execute(
                "UPDATE documents SET failure_reason=%s WHERE tenant_id=%s AND owner_id=%s AND doc_id=%s",
                (reason, self._t, owner_id, doc_id),
            )
            return cur.rowcount > 0


class PostgresSessions:
    def __init__(
        self, pool: Any, tenant_id: str = DEFAULT_TENANT, *, max_messages: int = 20,
        max_tokens: int = 2000, ttl_hours: float = 24.0,
    ) -> None:
        self._pool, self._t = pool, tenant_id
        self.max_messages = max(2, max_messages)
        self.max_tokens = max(1, max_tokens)
        self.ttl = timedelta(hours=ttl_hours)
        self._last_purge = 0.0
        self.purge_expired()

    def for_tenant(self, tenant_id: str) -> PostgresSessions:
        v = copy.copy(self)  # shares the pool; no startup purge per request
        v._t = tenant_id
        return v

    def _maybe_purge(self) -> None:
        if time.monotonic() - self._last_purge >= PURGE_EVERY_S:
            self.purge_expired()

    def purge_expired(self) -> int:
        self._last_purge = time.monotonic()
        with self._pool.connection() as c:
            cur = c.execute(
                "DELETE FROM sessions WHERE tenant_id=%s AND expires_at <= now()", (self._t,)
            )  # messages cascade
            return cur.rowcount

    def create(self, owner_id: str, max_per_owner: int | None = None) -> str:
        self._maybe_purge()
        sid = uuid.uuid4().hex
        now = datetime.now(UTC)
        with self._pool.connection() as c:
            # Serialise per owner so count + insert cannot race past the cap.
            c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"sess:{self._t}:{owner_id}",))
            if max_per_owner is not None:
                n = c.execute(
                    "SELECT count(*) FROM sessions WHERE tenant_id=%s AND owner_id=%s AND expires_at > now()",
                    (self._t, owner_id),
                ).fetchone()[0]
                if n >= max_per_owner:
                    raise SessionLimitError
            c.execute(
                "INSERT INTO sessions (tenant_id, owner_id, session_id, created_at, updated_at, expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (self._t, owner_id, sid, now, now, now + self.ttl),
            )
        return sid

    def exists(self, owner_id: str, sid: str) -> bool:
        self._maybe_purge()
        with self._pool.connection() as c:
            return c.execute(
                "SELECT 1 FROM sessions WHERE tenant_id=%s AND owner_id=%s AND session_id=%s "
                "AND expires_at > now()",
                (self._t, owner_id, sid),
            ).fetchone() is not None

    def messages(self, owner_id: str, sid: str) -> list[SessionMessage] | None:
        self._maybe_purge()
        with self._pool.connection() as c:
            if c.execute(
                "SELECT 1 FROM sessions WHERE tenant_id=%s AND owner_id=%s AND session_id=%s "
                "AND expires_at > now()",
                (self._t, owner_id, sid),
            ).fetchone() is None:
                return None
            rows = c.execute(
                "SELECT role, content, citation_ids, created_at FROM session_messages "
                "WHERE tenant_id=%s AND owner_id=%s AND session_id=%s ORDER BY id",
                (self._t, owner_id, sid),
            ).fetchall()
        msgs = [
            SessionMessage(
                tenant_id=self._t, role=r[0], content=r[1], created_at=r[3],
                citation_ids=json.loads(r[2]) if isinstance(r[2], str) else list(r[2]),
            )
            for r in rows
        ]
        return bound_messages(msgs, self.max_messages, self.max_tokens)

    def list_sessions(
        self, owner_id: str, limit: int, offset: int
    ) -> tuple[list[SessionSummary], int]:
        self._maybe_purge()
        with self._pool.connection() as c:
            total = c.execute(
                "SELECT count(*) FROM sessions WHERE tenant_id=%s AND owner_id=%s AND expires_at > now()",
                (self._t, owner_id),
            ).fetchone()[0]
            rows = c.execute(
                "SELECT s.session_id, s.updated_at, (SELECT left(m.content, %s) FROM session_messages m "
                "WHERE m.tenant_id=%s AND m.owner_id=%s AND m.session_id=s.session_id AND m.role='user' "
                "ORDER BY m.id LIMIT 1) FROM sessions s "
                "WHERE s.tenant_id=%s AND s.owner_id=%s AND s.expires_at > now() "
                "ORDER BY s.updated_at DESC, s.session_id LIMIT %s OFFSET %s",
                (FIRST_Q_CHARS, self._t, owner_id, self._t, owner_id, limit, offset),
            ).fetchall()
        return [SessionSummary(session_id=r[0], updated_at=r[1], first_question=r[2] or "")
                for r in rows], total

    def append_turn(
        self, owner_id: str, sid: str, question: str, answer: str, citation_ids: list[str],
    ) -> bool:
        cap = self.max_tokens * 2
        question, answer = _clip(question, cap), _clip(answer, cap)
        now = datetime.now(UTC)
        ids = json.dumps([str(i) for i in citation_ids])
        with self._pool.connection() as c:
            hit = c.execute(
                "UPDATE sessions SET updated_at=%s, expires_at=%s WHERE tenant_id=%s AND owner_id=%s "
                "AND session_id=%s AND expires_at > now()",
                (now, now + self.ttl, self._t, owner_id, sid),
            )
            if hit.rowcount != 1:
                return False
            for role, text, cites in (("user", question, "[]"), ("assistant", answer, ids)):
                c.execute(
                    "INSERT INTO session_messages (tenant_id, owner_id, session_id, role, content, "
                    "citation_ids, created_at) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)",
                    (self._t, owner_id, sid, role, text, cites, now),
                )
            rows = c.execute(
                "SELECT id, content FROM session_messages WHERE tenant_id=%s AND owner_id=%s "
                "AND session_id=%s ORDER BY id",
                (self._t, owner_id, sid),
            ).fetchall()
            keep = rows[-self.max_messages:]
            while len(keep) > 2 and sum(_approx_tokens(r[1]) for r in keep) > self.max_tokens:
                keep = keep[2:]
            drop = [r[0] for r in rows[: len(rows) - len(keep)]]
            if drop:
                c.execute(
                    "DELETE FROM session_messages WHERE tenant_id=%s AND owner_id=%s "
                    "AND session_id=%s AND id = ANY(%s)",
                    (self._t, owner_id, sid, drop),
                )
        return True

    def delete(self, owner_id: str, sid: str) -> bool:
        with self._pool.connection() as c:
            cur = c.execute(
                "DELETE FROM sessions WHERE tenant_id=%s AND owner_id=%s AND session_id=%s",
                (self._t, owner_id, sid),
            )
            return cur.rowcount > 0


__all__ = ["PostgresRegistry", "PostgresSessions"]
