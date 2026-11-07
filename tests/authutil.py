"""Test auth helpers: fixture secret and bearer headers."""

from app.core.auth import mint_token

TEST_SECRET = "test-fixture-secret-0123456789-abcdefghij"  # >= 32 bytes, tests only
TENANT = "t1"


def bearer(user: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {mint_token(user, TENANT, TEST_SECRET)}"}
