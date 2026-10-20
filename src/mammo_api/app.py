"""HTTP exposure of the MCP server: streamable HTTP plus API key, body limit, rate limit and CORS.

Middlewares are pure ASGI so they never buffer responses and pass lifespan events through untouched.
Order (outer to inner): CORS -> rate limit -> API key -> body limit -> MCP app.
"""
from __future__ import annotations

import hmac
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MCP_PATH = "/mcp"
HEALTH_PATH = "/health"


def _is_health_probe(scope: Scope) -> bool:
    """Only a bodiless GET/HEAD on /health is public; any other method there is treated like /mcp."""
    return scope["path"] == HEALTH_PATH and scope["method"] in ("GET", "HEAD")


@dataclass(frozen=True)
class HttpOptions:
    api_key: str
    max_body_bytes: int
    rate_limit_per_minute: int
    allowed_origins: tuple[str, ...]
    allowed_hosts: tuple[str, ...]
    trust_proxy_headers: bool = False


async def _send_json(send: Send, status: int, body: dict, headers: dict[str, str] | None = None) -> None:
    raw = json.dumps(body).encode()
    all_headers = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
    all_headers += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": all_headers})
    await send({"type": "http.response.body", "body": raw})


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


class ApiKeyMiddleware:
    """Requires `Authorization: Bearer <key>` or `X-API-Key: <key>` on every route except /health."""

    def __init__(self, app: ASGIApp, api_key: str) -> None:
        if len(api_key) < 24:
            raise ValueError("The API key must have at least 24 characters.")
        self._app, self._key = app, api_key.encode()

    def _presented(self, scope: Scope) -> bytes | None:
        auth = _header(scope, b"authorization")
        if auth and auth[:7].lower() == "bearer ":
            return auth[7:].strip().encode()
        key = _header(scope, b"x-api-key")
        return key.strip().encode() if key else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # CORS preflights are answered by the outer CORSMiddleware and never reach this point.
        if scope["type"] != "http" or _is_health_probe(scope):
            await self._app(scope, receive, send)
            return
        presented = self._presented(scope)
        if presented is None or not hmac.compare_digest(presented, self._key):
            await _send_json(send, 401, {"error": "unauthorized", "message": "Missing or invalid API key."},
                             {"WWW-Authenticate": 'Bearer realm="mammo-triage"'})
            return
        await self._app(scope, receive, send)


class BodyLimitMiddleware:
    """Rejects bodies above the limit with 413, by Content-Length or by counting streamed chunks."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self._app, self._max = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        declared = _header(scope, b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self._max):
            await _send_json(send, 413, {"error": "payload_too_large",
                                         "message": f"The request exceeds {self._max // 1_000_000} MB."})
            return
        # Without Content-Length (chunked), buffer up to the limit and replay the body to the app.
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            size += len(message.get("body", b""))
            if size > self._max:
                await _send_json(send, 413, {"error": "payload_too_large",
                                             "message": f"The request exceeds {self._max // 1_000_000} MB."})
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body, replayed = b"".join(chunks), False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self._app(scope, replay, send)


_LOOPBACK = {"127.0.0.1", "::1"}
_MAX_TRACKED_CLIENTS = 10_000


class RateLimitMiddleware:
    """Sliding one-minute window per client address on the MCP endpoint, kept in memory (single process).

    `CF-Connecting-IP` is only trusted when the connection itself comes from loopback (the local tunnel
    daemon); otherwise a direct client could forge it to get a fresh bucket per request.
    """

    def __init__(self, app: ASGIApp, per_minute: int, trust_proxy_headers: bool,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._app, self._limit, self._trust = app, per_minute, trust_proxy_headers
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def _client(self, scope: Scope) -> str:
        client = scope.get("client")
        address = client[0] if client else "unknown"
        if self._trust and address in _LOOPBACK:
            forwarded = _header(scope, b"cf-connecting-ip")
            if forwarded:
                return forwarded.strip()[:64]
        return address

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _is_health_probe(scope) or self._limit <= 0:
            await self._app(scope, receive, send)
            return
        now, key = self._clock(), self._client(scope)
        hits = self._hits.pop(key, None) or deque()
        self._hits[key] = hits  # re-insert: dict order becomes least-recently-used first
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= self._limit:
            retry = max(1, int(60 - (now - hits[0])) + 1)
            await _send_json(send, 429, {"error": "rate_limited", "message": "Too many requests, try again later."},
                             {"Retry-After": str(retry)})
            return
        hits.append(now)
        if len(self._hits) > _MAX_TRACKED_CLIENTS:  # bound memory: drop idle clients, then the oldest ones
            for stale in [k for k, v in self._hits.items() if not v or now - v[-1] >= 60]:
                del self._hits[stale]
            while len(self._hits) > _MAX_TRACKED_CLIENTS:  # least recently used first
                del self._hits[next(iter(self._hits))]
        await self._app(scope, receive, send)


async def _health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_http_app(mcp: MCPServer, options: HttpOptions) -> ASGIApp:
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=list(options.allowed_hosts),
                                         allowed_origins=list(options.allowed_origins))
    inner = mcp.streamable_http_app(streamable_http_path=MCP_PATH, json_response=True, stateless_http=True,
                                    max_request_body_size=options.max_body_bytes, transport_security=security)
    inner.router.routes.append(Route(HEALTH_PATH, _health, methods=["GET"]))
    app: ASGIApp = BodyLimitMiddleware(inner, options.max_body_bytes)
    app = ApiKeyMiddleware(app, options.api_key)
    app = RateLimitMiddleware(app, options.rate_limit_per_minute, options.trust_proxy_headers)
    return CORSMiddleware(app, allow_origins=list(options.allowed_origins), allow_methods=["GET", "POST", "OPTIONS"],
                          allow_headers=["authorization", "x-api-key", "content-type", "mcp-protocol-version",
                                         "mcp-session-id", "accept", "last-event-id"],
                          expose_headers=["mcp-session-id", "retry-after"], max_age=600)
