"""SQLite per-session chat memory (ADR-0002 section 5), same DB file as the registry.

Stores ONLY question text, answer text and citation ids; never retrieved chunks or tool output.
Every query filters by owner_id in SQL: a foreign or unknown session looks identical (None/False).
History is bounded on write and on read (last N messages and about T tokens, oldest dropped first,
whole turns at a time). Idle sessions expire after `ttl_hours`; purged lazily and at startup.
Synchronous: async callers wrap calls in run_blocking.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.core.llm import _approx_tokens
from app.store.sqlite import connect, init_db
from app.vectorstore.base import DEFAULT_TENANT

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_sessions_owner ON sessions(owner_id);
CREATE INDEX IF NOT EXISTS ix_sessions_expires ON sessions(expires_at);
CREATE TABLE IF NOT EXISTS session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL,
    citation_ids TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_session_messages_sid ON session_messages(session_id, id);
"""
PURGE_EVERY_S = 60.0
MARK = " ...[truncated]"


class SessionLimitError(Exception):
    pass


class SessionMessage(BaseModel):
    tenant_id: str = DEFAULT_TENANT
    role: str
    content: str
    citation_ids: list[str] = []
    created_at: datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - len(MARK)] + MARK


class SessionStore(Protocol):
    max_messages: int
    max_tokens: int

    def purge_expired(self) -> int: ...
    def create(self, owner_id: str, max_per_owner: int | None = None) -> str: ...
    def exists(self, owner_id: str, sid: str) -> bool: ...
    def messages(self, owner_id: str, sid: str) -> list[SessionMessage] | None: ...
    def append_turn(
        self, owner_id: str, sid: str, question: str, answer: str, citation_ids: list[str]
    ) -> bool: ...
    def delete(self, owner_id: str, sid: str) -> bool: ...
    def for_tenant(self, tenant_id: str) -> SessionStore: ...


def bound_messages(msgs: list[SessionMessage], max_messages: int, max_tokens: int) -> list[SessionMessage]:
    """Keep the last `max_messages`, then drop oldest whole turns while over `max_tokens`."""
    msgs = msgs[-max_messages:]
    while len(msgs) > 2 and sum(_approx_tokens(m.content) for m in msgs) > max_tokens:
        msgs = msgs[2:]  # the newest turn is always kept
    return msgs


class InMemorySessions:
    """Dict-backed SessionStore (same contract as SqliteSessions); used by unit tests."""

    def __init__(
        self, *, max_messages: int = 20, max_tokens: int = 2000, ttl_hours: float = 24.0,
        clock=_now,
    ) -> None:
        self.max_messages = max(2, max_messages)
        self.max_tokens = max(1, max_tokens)
        self.ttl = timedelta(hours=ttl_hours)
        self._now = clock
        self._s: dict[str, tuple[str, datetime, list[SessionMessage]]] = {}  # sid -> owner, exp, msgs
        self._tenant_of: dict[str, str] = {}  # sid -> tenant (shared by all tenant views)
        self._lock = threading.Lock()
        self._t = DEFAULT_TENANT

    def for_tenant(self, tenant_id: str) -> InMemorySessions:
        """View over the same storage; sessions of other tenants look unknown."""
        v = copy.copy(self)
        v._t = tenant_id
        return v

    def purge_expired(self) -> int:
        with self._lock:
            dead = [k for k, (_, exp, _) in self._s.items() if exp <= self._now()]
            for k in dead:
                del self._s[k]
                self._tenant_of.pop(k, None)
        return len(dead)

    def _live(self, owner_id: str, sid: str):
        row = self._s.get(sid)
        ok = row and row[0] == owner_id and row[1] > self._now() and self._tenant_of.get(sid) == self._t
        return row if ok else None

    def create(self, owner_id: str, max_per_owner: int | None = None) -> str:
        with self._lock:
            now = self._now()
            if max_per_owner is not None and sum(
                1 for k, (o, exp, _) in self._s.items()
                if o == owner_id and exp > now and self._tenant_of.get(k) == self._t
            ) >= max_per_owner:
                raise SessionLimitError
            sid = uuid.uuid4().hex
            self._s[sid] = (owner_id, now + self.ttl, [])
            self._tenant_of[sid] = self._t
        return sid

    def exists(self, owner_id: str, sid: str) -> bool:
        with self._lock:
            return self._live(owner_id, sid) is not None

    def messages(self, owner_id: str, sid: str) -> list[SessionMessage] | None:
        with self._lock:
            row = self._live(owner_id, sid)
            if row is None:
                return None
            return [m.model_copy() for m in bound_messages(row[2], self.max_messages, self.max_tokens)]

    def append_turn(
        self, owner_id: str, sid: str, question: str, answer: str, citation_ids: list[str],
    ) -> bool:
        cap = self.max_tokens * 2
        now = self._now()
        with self._lock:
            row = self._live(owner_id, sid)
            if row is None:
                return False
            msgs = row[2] + [
                SessionMessage(tenant_id=self._t, role="user", content=_clip(question, cap),
                               created_at=now),
                SessionMessage(tenant_id=self._t, role="assistant", content=_clip(answer, cap),
                               citation_ids=[str(i) for i in citation_ids], created_at=now),
            ]
            self._s[sid] = (owner_id, now + self.ttl,
                            bound_messages(msgs, self.max_messages, self.max_tokens))
        return True

    def delete(self, owner_id: str, sid: str) -> bool:
        with self._lock:
            row = self._s.get(sid)
            if row is None or row[0] != owner_id or self._tenant_of.get(sid) != self._t:
                return False
            del self._s[sid]
            self._tenant_of.pop(sid, None)
        return True


class SqliteSessions:
    def __init__(
        self, db_path: str | Path, *, max_messages: int = 20, max_tokens: int = 2000,
        ttl_hours: float = 24.0,
    ) -> None:
        self._path = Path(db_path)
        self.max_messages = max(2, max_messages)
        self.max_tokens = max(1, max_tokens)
        self.ttl = timedelta(hours=ttl_hours)
        self._last_purge = 0.0
        init_db(self._path, _SCHEMA)  # CREATE IF NOT EXISTS: safe on an existing registry DB
        self.purge_expired()  # at startup

    def for_tenant(self, tenant_id: str) -> SqliteSessions:
        """Legacy single-tenant store (tests/dev only): not tenant-aware, returns itself."""
        return self

    def _exp(self, now: datetime) -> str:
        return (now + self.ttl).isoformat()

    def _maybe_purge(self) -> None:
        # Lazy purge, throttled: reads already treat expired rows as unknown via _live.
        if time.monotonic() - self._last_purge >= PURGE_EVERY_S:
            self.purge_expired()

    def purge_expired(self) -> int:
        self._last_purge = time.monotonic()
        with connect(self._path) as c:
            ids = [r[0] for r in c.execute(
                "SELECT session_id FROM sessions WHERE expires_at <= ?", (_now().isoformat(),))]
            for sid in ids:  # explicit: foreign_keys pragma is off by default
                c.execute("DELETE FROM session_messages WHERE session_id=?", (sid,))
                c.execute("DELETE FROM sessions WHERE session_id=?", (sid,))
        return len(ids)

    def create(self, owner_id: str, max_per_owner: int | None = None) -> str:
        """Raises SessionLimitError when the owner already has `max_per_owner` live sessions."""
        self._maybe_purge()
        now = _now()
        sid = uuid.uuid4().hex
        with connect(self._path) as c:
            c.execute("BEGIN IMMEDIATE")  # count + insert atomically (no race past the cap)
            if max_per_owner is not None:
                n = c.execute(
                    "SELECT count(*) FROM sessions WHERE owner_id=? AND expires_at>?",
                    (owner_id, now.isoformat()),
                ).fetchone()[0]
                if n >= max_per_owner:
                    raise SessionLimitError
            c.execute("INSERT INTO sessions VALUES (?,?,?,?,?)",
                      (sid, owner_id, now.isoformat(), now.isoformat(), self._exp(now)))
        return sid

    def _live(self, c: sqlite3.Connection, owner_id: str, sid: str) -> bool:
        row = c.execute("SELECT expires_at FROM sessions WHERE session_id=? AND owner_id=?",
                        (sid, owner_id)).fetchone()
        return row is not None and row[0] > _now().isoformat()

    def exists(self, owner_id: str, sid: str) -> bool:
        self._maybe_purge()
        with connect(self._path) as c:
            return self._live(c, owner_id, sid)

    def messages(self, owner_id: str, sid: str) -> list[SessionMessage] | None:
        """Bounded history (oldest first) or None when unknown/foreign/expired."""
        self._maybe_purge()
        with connect(self._path) as c:
            if not self._live(c, owner_id, sid):
                return None
            rows = c.execute(
                "SELECT m.id, m.role, m.content, m.citation_ids, m.created_at FROM session_messages m "
                "JOIN sessions s ON s.session_id=m.session_id "
                "WHERE m.session_id=? AND s.owner_id=? ORDER BY m.id", (sid, owner_id)).fetchall()
        return self._bound(rows)

    def _bound(self, rows: list[tuple]) -> list[SessionMessage]:
        rows = rows[-self.max_messages:]
        while len(rows) > 2 and sum(_approx_tokens(r[2]) for r in rows) > self.max_tokens:
            rows = rows[2:]  # drop the oldest whole turn; the newest turn is always kept
        return [SessionMessage(role=r[1], content=r[2], citation_ids=json.loads(r[3]),
                               created_at=r[4]) for r in rows]

    def append_turn(
        self, owner_id: str, sid: str, question: str, answer: str, citation_ids: list[str],
    ) -> bool:
        """Store one Q/A turn, then trim the persisted rows to the bounds. False if not owner's."""
        cap = self.max_tokens * 2  # chars per message: one turn (2 messages) always fits the budget
        question, answer = _clip(question, cap), _clip(answer, cap)
        now = _now()
        with connect(self._path) as c:
            c.execute("BEGIN IMMEDIATE")  # owner/live check and writes in one write transaction
            if not self._live(c, owner_id, sid):
                return False
            ids = json.dumps([str(i) for i in citation_ids])
            for role, text, cites in (("user", question, "[]"), ("assistant", answer, ids)):
                c.execute(
                    "INSERT INTO session_messages (session_id, role, content, citation_ids, created_at) "
                    "VALUES (?,?,?,?,?)", (sid, role, text, cites, now.isoformat()))
            rows = c.execute("SELECT id, role, content, citation_ids, created_at FROM session_messages "
                             "WHERE session_id=? ORDER BY id", (sid,)).fetchall()
            keep = self._bound(rows)
            drop = len(rows) - len(keep)
            for r in rows[:drop]:
                c.execute("DELETE FROM session_messages WHERE id=?", (r[0],))
            c.execute("UPDATE sessions SET updated_at=?, expires_at=? WHERE session_id=? AND owner_id=?",
                      (now.isoformat(), self._exp(now), sid, owner_id))
        return True

    def delete(self, owner_id: str, sid: str) -> bool:
        with connect(self._path) as c:
            if c.execute("SELECT 1 FROM sessions WHERE session_id=? AND owner_id=?",
                         (sid, owner_id)).fetchone() is None:
                return False
            c.execute("DELETE FROM session_messages WHERE session_id=?", (sid,))
            c.execute("DELETE FROM sessions WHERE session_id=? AND owner_id=?", (sid, owner_id))
        return True
