"""Service auth (ADR-0003 section 5): HMAC-signed bearer user tokens and hashed tenant API keys.

Pure functions plus a `KeyStore` interface (in-memory here; Postgres-backed store comes with
T-026/T-029). No FastAPI objects: `api/deps.py` maps `AuthError` to a uniform 401.
Raw API keys are shown once at issue time and are never stored or logged.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, replace
from typing import Protocol

TOKEN_PREFIX = "v1"
KEY_PREFIX = "dck"
MIN_SECRET_BYTES = 32
DEFAULT_TOKEN_TTL_S = 3600
LAST_USED_THROTTLE_S = 60
MAX_CREDENTIAL_LEN = 1024
_ID = re.compile(r"[A-Za-z0-9_.@-]{1,64}")


class AuthError(Exception):
    """Any credential failure. Deliberately carries no reason (uniform 401, no oracle)."""


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    owner_id: str


@dataclass(frozen=True)
class ApiKeyRecord:
    key_id: str
    key_hash: str  # hex HMAC-SHA256(AUTH_TOKEN_SECRET, secret); never the raw secret
    tenant_id: str
    created_at: float
    revoked: bool = False
    last_used_at: float | None = None
    expires_at: float | None = None


class KeyStore(Protocol):
    def add(self, rec: ApiKeyRecord) -> None: ...
    def get(self, key_id: str) -> ApiKeyRecord | None: ...
    def revoke(self, key_id: str) -> bool: ...
    def touch(self, key_id: str, now: float) -> None: ...


class InMemoryKeyStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recs: dict[str, ApiKeyRecord] = {}

    def add(self, rec: ApiKeyRecord) -> None:
        with self._lock:
            self._recs[rec.key_id] = rec

    def get(self, key_id: str) -> ApiKeyRecord | None:
        with self._lock:
            return self._recs.get(key_id)

    def revoke(self, key_id: str) -> bool:
        with self._lock:
            rec = self._recs.get(key_id)
            if rec is None:
                return False
            self._recs[key_id] = replace(rec, revoked=True)
            return True

    def touch(self, key_id: str, now: float) -> None:
        with self._lock:
            rec = self._recs.get(key_id)
            if rec is not None:
                self._recs[key_id] = replace(rec, last_used_at=now)


def valid_id(value: str) -> bool:
    return bool(_ID.fullmatch(value))


def _key(secret: str) -> bytes:
    raw = secret.encode()
    if len(raw) < MIN_SECRET_BYTES:
        raise AuthError  # unconfigured/short secret: nothing can authenticate
    return raw


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sig(secret: str, signing_input: str) -> str:
    return _b64(hmac.new(_key(secret), signing_input.encode(), hashlib.sha256).digest())


def mint_token(
    sub: str, tenant_id: str, secret: str, ttl_s: int = DEFAULT_TOKEN_TTL_S, now: float | None = None
) -> str:
    if not valid_id(sub) or not valid_id(tenant_id):
        raise ValueError("invalid sub or tenant_id")
    iat = int(time.time() if now is None else now)
    payload = {"sub": sub, "tenant_id": tenant_id, "iat": iat, "exp": iat + int(ttl_s)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{TOKEN_PREFIX}.{body}"
    return f"{signing_input}.{_sig(secret, signing_input)}"


def verify_token(token: str, secret: str, now: float | None = None) -> Principal:
    try:
        if len(token) > MAX_CREDENTIAL_LEN:
            raise AuthError
        prefix, body, sig = token.split(".")
        if prefix != TOKEN_PREFIX:
            raise AuthError
        if not hmac.compare_digest(sig.encode(), _sig(secret, f"{prefix}.{body}").encode()):
            raise AuthError
        payload = json.loads(_unb64(body))
        sub, tenant, exp = payload["sub"], payload["tenant_id"], payload["exp"]
        if not (isinstance(sub, str) and isinstance(tenant, str) and isinstance(exp, int | float)):
            raise AuthError
        if not valid_id(sub) or not valid_id(tenant):
            raise AuthError
        if (time.time() if now is None else now) >= exp:
            raise AuthError
        return Principal(tenant_id=tenant, owner_id=sub)
    except AuthError:
        raise
    except (ValueError, KeyError, TypeError):  # malformed input: uniform failure
        raise AuthError from None


def hash_api_secret(api_secret: str, secret: str) -> str:
    return hmac.new(_key(secret), api_secret.encode(), hashlib.sha256).hexdigest()


def issue_api_key(
    store: KeyStore, tenant_id: str, secret: str, expires_at: float | None = None,
    now: float | None = None,
) -> str:
    """Create a key and return the raw `dck_<key_id>_<secret>` (the only time it exists)."""
    if not valid_id(tenant_id):
        raise ValueError("invalid tenant_id")
    key_id = secrets.token_hex(6)
    api_secret = _b64(secrets.token_bytes(32))
    store.add(ApiKeyRecord(
        key_id=key_id, key_hash=hash_api_secret(api_secret, secret), tenant_id=tenant_id,
        created_at=time.time() if now is None else now, expires_at=expires_at,
    ))
    return f"{KEY_PREFIX}_{key_id}_{api_secret}"


def rotate_api_key(store: KeyStore, key_id: str, secret: str) -> str:
    """Issue a new key for the same tenant and revoke the old one."""
    old = store.get(key_id)
    if old is None or old.revoked:
        raise AuthError
    new = issue_api_key(store, old.tenant_id, secret, old.expires_at)
    store.revoke(key_id)
    return new


def verify_api_key(
    store: KeyStore, raw: str, secret: str, now: float | None = None
) -> str:
    """Return the tenant_id for a live key or raise AuthError."""
    try:
        if len(raw) > MAX_CREDENTIAL_LEN:
            raise AuthError
        prefix, key_id, api_secret = raw.split("_", 2)
        if prefix != KEY_PREFIX:
            raise AuthError
    except ValueError:
        raise AuthError from None
    t = time.time() if now is None else now
    rec = store.get(key_id)
    candidate = hash_api_secret(api_secret, secret)
    # Compare against a dummy when the key is unknown so timing does not reveal existence.
    expected = rec.key_hash if rec else "0" * len(candidate)
    ok = hmac.compare_digest(candidate.encode(), expected.encode())
    if rec is None or not ok or rec.revoked or (rec.expires_at is not None and t >= rec.expires_at):
        raise AuthError
    if rec.last_used_at is None or t - rec.last_used_at >= LAST_USED_THROTTLE_S:
        store.touch(key_id, t)
    return rec.tenant_id
