"""FastAPI dependencies: bearer auth (ADR-0003 section 5) and app-wide services (ADR-0002 section 1).

`current_user` returns the owner id and `current_tenant` the tenant id, both from the verified
credential only (never body/query/other headers). Service getters return tenant-bound views.
Routers never read headers themselves. Tests use
`app.dependency_overrides[current_principal]` and may override the service getters.
"""

from typing import Annotated, NewType

from fastapi import Depends, Header, HTTPException, Request, Response

from ..core.auth import (
    KEY_PREFIX,
    AuthError,
    InMemoryKeyStore,
    KeyStore,
    Principal,
    valid_id,
    verify_api_key,
    verify_token,
)
from ..core.config import get_settings
from ..core.limits import GLOBAL_KEY, Lease, RateLimiter, SpendCapError, SpendLedger
from ..services.ingestion import IngestionService
from ..store.bm25 import Bm25Store
from ..store.parents import SqliteParents
from ..store.registry import Registry
from ..store.sessions import SqliteSessions
from ..store.spend import SqliteSpend
from ..vectorstore.sync import SyncVectors

UserId = NewType("UserId", str)
TenantId = NewType("TenantId", str)

class ApiError(HTTPException):
    """HTTPException carrying a machine-readable `code` for the {detail, code} body."""

    def __init__(
        self, status_code: int, code: str, detail: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code


def get_key_store(request: Request) -> KeyStore:
    st = request.app.state
    if getattr(st, "key_store", None) is None:
        st.key_store = InMemoryKeyStore()  # Postgres-backed store replaces this (T-026/T-029)
    return st.key_store


def _unauthorized() -> ApiError:
    return ApiError(
        401, "unauthorized", "Valid credentials are required", {"WWW-Authenticate": "Bearer"}
    )


def current_principal(
    key_store: Annotated[KeyStore, Depends(get_key_store)],
    authorization: Annotated[str | None, Header()] = None,
    x_on_behalf_of: Annotated[str | None, Header()] = None,
) -> Principal:
    """Bearer user token, or `dck_` tenant API key + X-On-Behalf-Of. Uniform 401 on any failure."""
    if not authorization:
        raise _unauthorized()
    scheme, _, cred = authorization.partition(" ")
    cred = cred.strip()
    if scheme.lower() != "bearer" or not cred:
        raise _unauthorized()
    secret = get_settings().auth_token_secret.get_secret_value()
    try:
        if cred.startswith(f"{KEY_PREFIX}_"):
            if not x_on_behalf_of or not valid_id(x_on_behalf_of):
                raise AuthError
            tenant = verify_api_key(key_store, cred, secret)
            return Principal(tenant_id=tenant, owner_id=x_on_behalf_of)
        return verify_token(cred, secret)
    except AuthError:
        raise _unauthorized() from None


def current_user(principal: Annotated[Principal, Depends(current_principal)]) -> UserId:
    return UserId(principal.owner_id)


def current_tenant(principal: Annotated[Principal, Depends(current_principal)]) -> TenantId:
    """Tenant of the verified credential. API-key owner ids (X-On-Behalf-Of) live inside this
    tenant only: every store below is filtered by (tenant, owner), so another tenant's owner id
    resolves to nothing (404)."""
    return TenantId(principal.tenant_id)


CurrentTenant = Annotated[TenantId, Depends(current_tenant)]


def get_registry(request: Request, tenant: CurrentTenant) -> Registry:
    return request.app.state.registry.for_tenant(tenant)


def get_store(request: Request, tenant: CurrentTenant) -> SyncVectors:
    return request.app.state.store.for_tenant(tenant)


def get_parents(request: Request, tenant: CurrentTenant) -> SqliteParents:
    return request.app.state.parents.for_tenant(tenant)


def get_bm25(request: Request, tenant: CurrentTenant) -> Bm25Store | None:
    b = getattr(request.app.state, "bm25", None)
    return b.for_tenant(tenant) if b is not None else None


def get_sessions(request: Request, tenant: CurrentTenant) -> SqliteSessions:
    return request.app.state.sessions.for_tenant(tenant)


def get_ingestion(request: Request, tenant: CurrentTenant) -> IngestionService:
    return request.app.state.ingestion.for_tenant(tenant)


CurrentUser = Annotated[UserId, Depends(current_user)]
RegistryDep = Annotated[Registry, Depends(get_registry)]
StoreDep = Annotated[SyncVectors, Depends(get_store)]
ParentsDep = Annotated[SqliteParents, Depends(get_parents)]
Bm25Dep = Annotated[Bm25Store | None, Depends(get_bm25)]
SessionsDep = Annotated[SqliteSessions, Depends(get_sessions)]
IngestionDep = Annotated[IngestionService, Depends(get_ingestion)]


MAX_JSON_BYTES = 64 * 1024


def json_body_limit(request: Request) -> None:
    """JSON bodies are capped at 64 KB (Content-Length check)."""
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > MAX_JSON_BYTES:
        raise ApiError(413, "too_large", "Request body is too large")


def get_limiter(request: Request) -> RateLimiter:
    st = request.app.state
    if getattr(st, "limiter", None) is None:
        st.limiter = RateLimiter(get_settings().rate_limit_per_min)
    return st.limiter


def get_global_limiter(request: Request) -> RateLimiter:
    st = request.app.state
    if getattr(st, "global_limiter", None) is None:
        st.global_limiter = RateLimiter(get_settings().global_rate_limit_per_min, max_users=1)
    return st.global_limiter


def get_ledger(request: Request) -> SpendLedger:
    st = request.app.state
    if getattr(st, "ledger", None) is None:
        s = get_settings()
        st.ledger = SpendLedger(
            SqliteSpend(s.db_path), s.daily_usd_cap, global_cap_usd=s.global_daily_usd_cap,
            tenant_cap_usd=s.tenant_daily_usd_cap,
        )
    return st.ledger


class Guard:
    """Rate limits already passed. `acquire()` (call AFTER validation/doc resolution, before any
    LLM call or stream) checks the daily caps and reserves the run's max USD; `finish(usd)`
    settles it (no-op if never acquired, idempotent)."""

    def __init__(
        self, ledger: SpendLedger, user: str, amount: float, headers: dict[str, str],
        tenant: str | None = None,
    ):
        self._ledger, self._user, self._amount, self._tenant = ledger, user, amount, tenant
        self.headers = headers
        self.lease: Lease | None = None

    def acquire(self) -> None:
        if self.lease is not None:
            return
        try:
            self.lease = self._ledger.acquire(self._user, self._amount, self._tenant)
        except SpendCapError as e:
            code = {"global": "global_spend_cap_exceeded", "tenant": "tenant_spend_cap_exceeded"}.get(
                e.scope, "spend_cap_exceeded"
            )
            msg = (
                "Daily spend cap for your organization reached; try again after 00:00 UTC"
                if e.scope == "tenant" else "Daily spend cap reached"
            )
            raise ApiError(429, code, msg, self.headers) from None

    def finish(self, usd: float = 0.0) -> None:
        if self.lease is not None:
            self.lease.finish(usd)


def llm_guard(max_usd_setting: str):
    """Dependency factory for LLM endpoints: per-user then global rate limit, before any work.

    Rejections are plain JSON 429s carrying the rate-limit headers.
    """

    def dep(
        response: Response, user: CurrentUser, tenant: CurrentTenant,
        limiter: Annotated[RateLimiter, Depends(get_limiter)],
        glimiter: Annotated[RateLimiter, Depends(get_global_limiter)],
        ledger: Annotated[SpendLedger, Depends(get_ledger)],
    ) -> Guard:
        d = limiter.hit(f"{tenant}:{user}")  # same owner id in two tenants = two buckets
        hdrs = d.headers()
        if not d.allowed:
            raise ApiError(429, "rate_limited", "Too many requests; retry later", hdrs)
        g = glimiter.hit(GLOBAL_KEY)
        if not g.allowed:
            raise ApiError(
                429, "global_rate_limited", "Service is busy; retry later",
                {**hdrs, "Retry-After": g.headers()["Retry-After"]},
            )
        for k, v in hdrs.items():
            response.headers[k] = v
        return Guard(ledger, user, float(getattr(get_settings(), max_usd_setting)), hdrs, tenant)

    return dep


QueryLease = Annotated[Guard, Depends(llm_guard("agent_max_usd"))]
SummarizeLease = Annotated[Guard, Depends(llm_guard("summary_max_usd"))]
