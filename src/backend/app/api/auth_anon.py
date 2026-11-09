"""POST /v1/auth/anonymous: mint a short-lived token for a fresh anonymous owner in the demo tenant.

Public (no credential) so it is rate limited per client IP. The IP comes from the `TRUSTED_PROXY`
header only when that setting is set (else the socket peer), so a client cannot spoof it by
sending X-Forwarded-For directly. Token values are never logged. Anonymous owners are cleaned up
by the existing idle-session TTL (SESSION_TTL_HOURS, default 24 h); documents follow the tenant
retention job (see docs/deploy.md).
"""

import ipaddress
import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..core.auth import mint_token
from ..core.config import get_settings
from ..core.limits import GLOBAL_KEY, RateLimiter
from .deps import ApiError

router = APIRouter(prefix="/v1/auth", tags=["auth"])
ANON_WINDOW_S = 3600.0


class AnonymousToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int  # unix seconds


def normalize_ip(text: str) -> str | None:
    """Limiter key: IPv4-mapped IPv6 -> plain IPv4; other IPv6 -> its /64 network; None if invalid."""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def client_ip(request: Request) -> str:
    header = get_settings().trusted_proxy.strip()
    if header:
        raw = request.headers.get(header, "")
        key = normalize_ip(raw.split(",")[-1].strip())  # rightmost = appended by our own proxy
        if key:
            return key
    peer = request.client.host if request.client else ""
    return normalize_ip(peer) or peer or "unknown"


def get_anon_limiter(request: Request) -> RateLimiter:
    st = request.app.state
    if getattr(st, "anon_limiter", None) is None:
        st.anon_limiter = RateLimiter(get_settings().anon_mint_rate_per_hour, window_s=ANON_WINDOW_S)
    return st.anon_limiter


def get_anon_global_limiter(request: Request) -> RateLimiter:
    st = request.app.state
    if getattr(st, "anon_global_limiter", None) is None:
        st.anon_global_limiter = RateLimiter(
            get_settings().anon_mint_global_per_hour, window_s=ANON_WINDOW_S, max_users=1
        )
    return st.anon_global_limiter


@router.post("/anonymous", response_model=AnonymousToken)
def mint_anonymous(
    request: Request, limiter: Annotated[RateLimiter, Depends(get_anon_limiter)],
    glimiter: Annotated[RateLimiter, Depends(get_anon_global_limiter)],
) -> AnonymousToken:
    d = limiter.hit(client_ip(request))
    if not d.allowed:
        raise ApiError(429, "rate_limited", "Too many tokens requested; retry later", d.headers())
    g = glimiter.hit(GLOBAL_KEY)  # after the per-IP check: IP-limited requests do not drain it
    if not g.allowed:
        raise ApiError(
            429, "global_rate_limited", "Service is busy; retry later", g.headers()
        )
    s = get_settings()
    now = int(time.time())
    token = mint_token(
        f"anon-{secrets.token_hex(12)}", s.demo_tenant_id, s.auth_token_secret.get_secret_value(),
        ttl_s=s.anon_token_ttl_seconds, now=now,
    )
    return AnonymousToken(access_token=token, expires_at=now + s.anon_token_ttl_seconds)
