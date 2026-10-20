"""HTTP exposure: API key, body limit, rate limit, CORS, DNS rebinding and a full MCP round trip."""
import base64

import pytest
from starlette.testclient import TestClient

from mammo_api import HttpOptions, RateLimitMiddleware, create_http_app
from mammo_mcp import McpOptions, create_mcp_server

from .fakes import PNG_BYTES, handlers

KEY = "k" * 32
ORIGIN = "http://localhost:5173"
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json",
               "mcp-protocol-version": "2025-06-18"}


def make_client(rate=100, max_body=200_000):
    mcp = create_mcp_server(handlers(), McpOptions(allow_local_paths=False, max_image_bytes=100_000))
    app = create_http_app(mcp, HttpOptions(api_key=KEY, max_body_bytes=max_body, rate_limit_per_minute=rate,
                                           allowed_origins=(ORIGIN,), allowed_hosts=("testserver", "127.0.0.1:*")))
    return TestClient(app)


def rpc(method, params=None, id_=1):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


INIT = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "test", "version": "1"}})


def test_health_is_public_and_minimal():
    with make_client() as c:
        r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"authorization": "Bearer wrong"}, {"x-api-key": "k" * 31},
                                     {"authorization": "Basic " + KEY}])
def test_mcp_requires_valid_key(headers):
    with make_client() as c:
        r = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, **headers})
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    assert KEY not in r.text


@pytest.mark.parametrize("auth", [{"authorization": f"Bearer {KEY}"}, {"x-api-key": KEY}])
def test_valid_key_initializes_and_calls_tool(auth):
    with make_client() as c:
        init = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, **auth})
        call = c.post("/mcp", headers={**MCP_HEADERS, **auth}, json=rpc("tools/call", {
            "name": "mammo_analyze_mammogram", "arguments": {"image_base64": base64.b64encode(PNG_BYTES).decode()}},
            2))
    assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "mammogram_triage_mcp"
    assert call.status_code == 200
    assert call.json()["result"]["structuredContent"]["overall_level"] == "high_priority"


def test_body_over_limit_is_413():
    with make_client(max_body=1_000) as c:
        r = c.post("/mcp", content=b"x" * 2_000, headers={**MCP_HEADERS, "x-api-key": KEY})
    assert r.status_code == 413


def test_chunked_body_over_limit_is_413():
    def chunks():
        for _ in range(5):
            yield b"x" * 400
    with make_client(max_body=1_000) as c:
        r = c.post("/mcp", content=chunks(), headers={**MCP_HEADERS, "x-api-key": KEY})
    assert r.status_code == 413


def test_rate_limit_returns_429_with_retry_after():
    with make_client(rate=2) as c:
        codes = [c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "x-api-key": KEY}).status_code for _ in range(3)]
        last = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "x-api-key": KEY})
        health = c.get("/health").status_code
    assert codes == [200, 200, 429] and int(last.headers["retry-after"]) >= 1
    assert health == 200  # /health never consumes or is blocked by the MCP budget


def test_unauthenticated_requests_also_consume_the_budget():
    with make_client(rate=2) as c:
        codes = [c.post("/mcp", json=INIT, headers=MCP_HEADERS).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


def test_non_preflight_options_needs_the_key_and_is_rate_limited():
    with make_client(rate=1) as c:
        first = c.request("OPTIONS", "/mcp", content=b"x" * 500)
        second = c.request("OPTIONS", "/mcp")
    assert first.status_code == 401 and second.status_code == 429


def _client_at(address: str, per_minute: int = 1):
    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})
    return TestClient(RateLimitMiddleware(ok, per_minute, True), client=(address, 5000))


def test_proxy_header_is_trusted_only_from_loopback():
    local = _client_at("127.0.0.1")
    assert [local.get("/mcp", headers={"cf-connecting-ip": ip}).status_code for ip in ("1.1.1.1", "2.2.2.2")] == [204, 204]
    remote = _client_at("203.0.113.9")
    assert [remote.get("/mcp", headers={"cf-connecting-ip": ip}).status_code for ip in ("1.1.1.1", "2.2.2.2")] == [204, 429]


def test_rate_limit_window_slides():
    now = [0.0]

    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    c = TestClient(RateLimitMiddleware(ok, 1, False, clock=lambda: now[0]))  # no lifespan for this bare app
    first, second = c.get("/").status_code, c.get("/").status_code
    now[0] = 61
    third = c.get("/").status_code
    assert (first, second, third) == (204, 429, 204)


def test_cors_preflight_allows_only_the_pwa_origin():
    with make_client() as c:
        good = c.options("/mcp", headers={"origin": ORIGIN, "access-control-request-method": "POST",
                                          "access-control-request-headers": "authorization,content-type"})
        bad = c.options("/mcp", headers={"origin": "https://evil.example", "access-control-request-method": "POST"})
    assert good.status_code == 200 and good.headers["access-control-allow-origin"] == ORIGIN
    assert "access-control-allow-origin" not in bad.headers


def test_foreign_origin_is_rejected_even_with_key():
    with make_client() as c:
        r = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "x-api-key": KEY, "origin": "https://evil.example"})
    assert r.status_code == 403


def test_foreign_host_is_rejected():
    with make_client() as c:
        r = c.post("/mcp", json=INIT, headers={**MCP_HEADERS, "x-api-key": KEY, "host": "attacker.example"})
    assert r.status_code == 421 or r.status_code == 400


def test_short_api_key_is_refused_at_startup():
    mcp = create_mcp_server(handlers(), McpOptions(False, 1000))
    with pytest.raises(ValueError):
        create_http_app(mcp, HttpOptions("short", 1000, 10, (ORIGIN,), ("testserver",)))


@pytest.mark.parametrize("method", ["POST", "PUT", "OPTIONS"])
def test_health_is_public_only_for_get_and_head(method):
    with make_client(rate=100) as c:
        assert c.head("/health").status_code == 200
        r = c.request(method, "/health", content=b"x" * 500)
    assert r.status_code == 401
