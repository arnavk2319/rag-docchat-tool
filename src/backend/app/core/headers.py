"""Pure-ASGI middleware: hardening headers on every /v1 response.

Not BaseHTTPMiddleware, so streaming (SSE) bodies pass through untouched; only the response
start message is edited. `Cache-Control` is set only when the route did not choose one (SSE
keeps its own `no-cache`).
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class V1SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/v1"):
            await self.app(scope, receive, send)
            return

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                h["X-Content-Type-Options"] = "nosniff"
                if "cache-control" not in h:
                    h["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, wrapped)
