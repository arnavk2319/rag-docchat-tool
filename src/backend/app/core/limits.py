"""Per-user abuse limits: sliding-window rate limiter and daily USD cap with reservations.

Both are per-process and keyed by the authenticated owner id. State is bounded: the limiter
evicts idle users and caps tracked users (attacker-chosen ids cannot grow memory without
bound); reservations expire. Clocks are injectable for tests. Nothing here logs user content.
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

WINDOW_S = 60.0
MAX_TRACKED_USERS = 10_000
RESERVATION_TTL_S = 900.0
GLOBAL_KEY = "*"  # cannot collide with a user id (ids match [A-Za-z0-9_.@-]+)


class SpendCapError(Exception):
    """Daily cap reached; `scope` is "user", "tenant" or "global"."""

    def __init__(self, scope: str) -> None:
        super().__init__(scope)
        self.scope = scope


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_s: int  # seconds until a slot frees (allowed: until window is empty)

    def headers(self) -> dict[str, str]:
        h = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_s),
        }
        if not self.allowed:
            h["Retry-After"] = str(max(1, self.reset_s))
        return h


class RateLimiter:
    def __init__(
        self,
        limit: int,
        *,
        window_s: float = WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
        max_users: int = MAX_TRACKED_USERS,
    ) -> None:
        self.limit, self.window, self._clock, self._max = limit, window_s, clock, max_users
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._next_sweep = clock() + window_s
        self._full_until = 0.0

    def __len__(self) -> int:
        return len(self._hits)

    def _sweep(self, now: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.window]:
            del self._hits[k]
        self._next_sweep = now + self.window

    def hit(self, user: str) -> RateDecision:
        with self._lock:
            now = self._clock()
            q = self._hits.get(user)
            if q is None and len(self._hits) >= self._max and now >= self._full_until:
                self._sweep(now)  # evicts idle users only
                if len(self._hits) >= self._max:
                    self._full_until = now + 1.0  # do not rescan on every flooded request
            elif now >= self._next_sweep:
                self._sweep(now)
            q = self._hits.get(user)  # may have been swept (idle)
            if q is None and len(self._hits) >= self._max:
                # Table full of active users: refuse the NEW id (never evict victims' windows).
                soonest = min(h[-1] for h in self._hits.values()) + self.window - now
                return RateDecision(False, self.limit, 0, max(1, math.ceil(soonest)))
            if q is None:
                q = self._hits[user] = deque()
            self._hits.move_to_end(user)
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return RateDecision(False, self.limit, 0, math.ceil(q[0] + self.window - now))
            q.append(now)
            return RateDecision(
                True, self.limit, self.limit - len(q), math.ceil(q[0] + self.window - now)
            )


class SpendStore(Protocol):
    def spent(self, owner_id: str, day: str) -> float: ...
    def add(self, owner_id: str, day: str, usd: float) -> None: ...


def tenant_key(tenant_id: str) -> str:
    return f"#t:{tenant_id}"  # "#" is not a valid id char, so it cannot collide with a user key


def utc_day(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m-%d")


@dataclass
class Lease:
    """One in-flight LLM run: holds a reservation until `finish(actual_usd)` (idempotent)."""

    ledger: SpendLedger
    owner: str
    lease_id: str
    amount: float
    headers: dict[str, str] = field(default_factory=dict)
    done: bool = False
    tenant_key: str | None = None

    @property
    def keys(self) -> tuple[str, ...]:
        return (self.owner, GLOBAL_KEY) + ((self.tenant_key,) if self.tenant_key else ())

    def finish(self, usd: float = 0.0) -> None:
        self.ledger.settle(self, usd)


class SpendLedger:
    """Daily cap check + reserve (per-run max USD), per user and under one global key, so
    concurrent runs cannot all slip past. A run that straddles UTC midnight is billed to the
    day on which it settles."""

    def __init__(
        self,
        store: SpendStore,
        cap_usd: float,
        *,
        global_cap_usd: float = float("inf"),
        tenant_cap_usd: float = float("inf"),
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if cap_usd <= 0 or global_cap_usd <= 0 or tenant_cap_usd <= 0:
            raise ValueError("spend caps must be > 0")
        self._store, self.cap, self.global_cap = store, cap_usd, global_cap_usd
        self.tenant_cap = tenant_cap_usd
        self._now, self._clock = now, clock
        self._held: dict[str, dict[str, tuple[float, float]]] = {}  # key -> id -> (usd, t)
        self._lock = threading.Lock()

    def reserved(self, owner: str) -> float:
        with self._lock:
            return sum(a for a, _ in self._held.get(owner, {}).values())

    def _over(self, key: str, cap: float, t: float, day: str) -> bool:
        mine = self._held.setdefault(key, {})
        for k in [k for k, (_, t0) in mine.items() if t - t0 > RESERVATION_TTL_S]:
            del mine[k]  # leaked reservations (response never iterated) expire
        over = self._store.spent(key, day) + sum(a for a, _ in mine.values()) >= cap
        if not mine:
            self._held.pop(key, None)
        return over

    def acquire(self, owner: str, amount: float, tenant: str | None = None) -> Lease:
        """Raises SpendCapError when spent + in-flight reservations >= cap (user, tenant or global).

        With `tenant`, the user key is namespaced by it (same owner id in two tenants is two users)
        and the tenant's total is capped too."""
        tkey = None if tenant is None else tenant_key(tenant)
        if tenant is not None:
            owner = f"{tenant}:{owner}"
        with self._lock:
            t, day = self._clock(), utc_day(self._now())
            if self._over(owner, self.cap, t, day):
                raise SpendCapError("user")
            if tkey is not None and self._over(tkey, self.tenant_cap, t, day):
                raise SpendCapError("tenant")
            if self._over(GLOBAL_KEY, self.global_cap, t, day):
                raise SpendCapError("global")
            lid, amt = uuid.uuid4().hex, max(0.0, amount)
            lease = Lease(self, owner, lid, amount, tenant_key=tkey)
            for key in lease.keys:
                self._held.setdefault(key, {})[lid] = (amt, t)
            return lease

    def settle(self, lease: Lease, usd: float) -> None:
        with self._lock:
            if lease.done:
                return
            lease.done = True
            for key in lease.keys:
                mine = self._held.get(key, {})
                mine.pop(lease.lease_id, None)
                if not mine:
                    self._held.pop(key, None)
        if usd > 0 and math.isfinite(usd):
            day = utc_day(self._now())
            for key in lease.keys:  # sync on purpose: must finish during cancel
                self._store.add(key, day, usd)
