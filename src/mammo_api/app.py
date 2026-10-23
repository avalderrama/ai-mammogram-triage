"""HTTP exposure of the MCP server: streamable HTTP plus authentication, body limit, rate limit and CORS.

Middlewares are pure ASGI so they never buffer responses and pass lifespan events through untouched.
Order (outer to inner): CORS -> rate limit -> authentication -> body limit -> MCP app (plus /health and /usage).
"""
from __future__ import annotations

import hmac
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any, Protocol

import anyio.to_thread
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from mammo_application.abstractions import DailyUsage

from .access import ACCESS_JWT_HEADER

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

MCP_PATH = "/mcp"
HEALTH_PATH = "/health"
USAGE_PATH = "/usage"


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
    access: AccessTokenVerifier | None = None
    # The usage report (/usage) is shown only to these Cloudflare Access e-mails; empty = nobody.
    admin_emails: tuple[str, ...] = ()
    usage_report: Callable[[int], list[DailyUsage]] | None = None


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


class AccessTokenVerifier(Protocol):
    def identify(self, token: str) -> str | None:
        """The identity in the token issued by the gateway in front of the server, or None when the token is not
        valid. May block on I/O."""


class AuthenticationMiddleware:
    """Requires, on every route except /health, either the API key (`Authorization: Bearer <key>` or
    `X-API-Key: <key>`) or, when a verifier is configured, a valid Cloudflare Access token. The key serves technical
    clients; browsers that went through Access are already identified by it and need no key. The Access identity
    (e-mail) is left in the request state as `identity` (None with the key); it is never logged."""

    def __init__(self, app: ASGIApp, api_key: str, access: AccessTokenVerifier | None = None) -> None:
        if len(api_key) < 24:
            raise ValueError("The API key must have at least 24 characters.")
        self._app, self._key, self._access = app, api_key.encode(), access

    def _presented_key(self, scope: Scope) -> bytes | None:
        auth = _header(scope, b"authorization")
        if auth and auth[:7].lower() == "bearer ":
            return auth[7:].strip().encode()
        key = _header(scope, b"x-api-key")
        return key.strip().encode() if key else None

    async def _authenticate(self, scope: Scope) -> tuple[bool, str | None]:
        """Whether the request may go on, and the Access identity behind it (None with the API key)."""
        presented = self._presented_key(scope)
        if presented is not None and hmac.compare_digest(presented, self._key):
            return True, None
        token = _header(scope, ACCESS_JWT_HEADER) if self._access else None
        if not token:
            return False, None
        # Signature checks are CPU work and a key-set refresh is network I/O: keep both off the event loop.
        identity = await anyio.to_thread.run_sync(self._access.identify, token.strip())
        return identity is not None, identity

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # CORS preflights are answered by the outer CORSMiddleware and never reach this point.
        if scope["type"] != "http" or _is_health_probe(scope):
            await self._app(scope, receive, send)
            return
        allowed, identity = await self._authenticate(scope)
        if not allowed:
            await _send_json(send, 401, {"error": "unauthorized", "message": "Missing or invalid credentials."},
                             {"WWW-Authenticate": 'Bearer realm="mammo-triage"'})
            return
        scope.setdefault("state", {})["identity"] = identity
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


_USAGE_TEXT = {
    "en": {"title": "Pilot usage",
           "note": "Analyses per day. Counts only: no images, results or identities are kept.",
           "columns": ("Day", "Analyses", "People", "High priority", "Review", "No suspicious findings",
                       "Not a mammogram", "Errors", "Views", "Views with heatmap", "Shadow gate: views",
                       "Only the current gate accepts", "Only the shadow gate accepts"),
           "total": "Total", "empty": "No analyses yet."},
    "es": {"title": "Uso del piloto",
           "note": "Análisis por día. Solo conteos: no se guardan imágenes, resultados ni identidades.",
           "columns": ("Día", "Análisis", "Personas", "Alta prioridad", "Revisar", "Sin hallazgos",
                       "No es mamografía", "Errores", "Vistas", "Vistas con mapa de calor", "Filtro sombra: vistas",
                       "Solo el filtro actual acepta", "Solo el filtro sombra acepta"),
           "total": "Total", "empty": "Todavía no hay análisis."},
}
_USAGE_FIELDS = ("analyses", "people", "high_priority", "review", "no_suspicious_findings", "not_a_mammogram",
                 "errors", "views", "detailed_views", "gate_compared", "gate_only_current", "gate_only_shadow")


def _usage_html(days: list[DailyUsage], language: str) -> str:
    t = _USAGE_TEXT[language]
    head = "".join(f"<th>{c}</th>" for c in t["columns"])
    rows = "".join(f"<tr><td>{d.day}</td>" + "".join(f"<td>{getattr(d, f)}</td>" for f in _USAGE_FIELDS) + "</tr>"
                   for d in days)
    # People are distinct per day only, so their total is left blank rather than summed.
    totals = "".join(f"<td>{'' if f == 'people' else sum(getattr(d, f) for d in days)}</td>" for f in _USAGE_FIELDS)
    body = (f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody>"
            f"<tfoot><tr><td>{t['total']}</td>{totals}</tr></tfoot></table>") if days else f"<p>{t['empty']}</p>"
    return (f"<!doctype html><html lang='{language}'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{t['title']}</title><style>body{{font-family:system-ui,sans-serif;margin:1.5rem}}"
            "table{border-collapse:collapse;font-variant-numeric:tabular-nums}"
            "th,td{border:1px solid #9ca3af;padding:.35rem .6rem;text-align:right}"
            "th:first-child,td:first-child{text-align:left}tfoot td{font-weight:600}</style></head><body>"
            f"<h1>{t['title']}</h1><p>{t['note']}</p>{body}</body></html>")


def _usage_endpoint(options: HttpOptions) -> Callable[[Request], Awaitable[Response]]:
    admins = {email.strip().lower() for email in options.admin_emails if email.strip()}

    async def usage(request: Request) -> Response:
        identity = getattr(request.state, "identity", None)
        if options.usage_report is None or identity not in admins:
            return JSONResponse({"error": "forbidden", "message": "Only the pilot administrator can see usage."},
                                status_code=403)
        param = request.query_params.get("days", "30")
        days = int(param) if param.isascii() and param.isdigit() and len(param) <= 4 else 30
        wanted = request.query_params.get("lang")
        language = wanted if wanted in _USAGE_TEXT else (
            "es" if request.headers.get("accept-language", "").lower().startswith("es") else "en")
        report = await anyio.to_thread.run_sync(options.usage_report, days)
        return HTMLResponse(_usage_html(report, language), headers={"Cache-Control": "no-store"})
    return usage


def create_http_app(mcp: MCPServer, options: HttpOptions) -> ASGIApp:
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                         allowed_hosts=list(options.allowed_hosts),
                                         allowed_origins=list(options.allowed_origins))
    inner = mcp.streamable_http_app(streamable_http_path=MCP_PATH, json_response=True, stateless_http=True,
                                    max_request_body_size=options.max_body_bytes, transport_security=security)
    inner.router.routes.append(Route(HEALTH_PATH, _health, methods=["GET"]))
    inner.router.routes.append(Route(USAGE_PATH, _usage_endpoint(options), methods=["GET"]))
    app: ASGIApp = BodyLimitMiddleware(inner, options.max_body_bytes)
    app = AuthenticationMiddleware(app, options.api_key, options.access)
    app = RateLimitMiddleware(app, options.rate_limit_per_minute, options.trust_proxy_headers)
    return CORSMiddleware(app, allow_origins=list(options.allowed_origins), allow_methods=["GET", "POST", "OPTIONS"],
                          allow_headers=["authorization", "x-api-key", "content-type", "mcp-protocol-version",
                                         "mcp-session-id", "accept", "last-event-id"],
                          expose_headers=["mcp-session-id", "retry-after"], max_age=600)
