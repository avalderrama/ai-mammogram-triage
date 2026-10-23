"""The Access identity reaches the analysis (to count people) and /usage is shown only to the administrator."""
import base64

from starlette.testclient import TestClient

from mammo_api import CloudflareAccessVerifier, HttpOptions, create_http_app
from mammo_application.abstractions import DailyUsage
from mammo_mcp import McpOptions, create_mcp_server

from .fakes import PNG_BYTES, handlers
from .test_access_auth import AUD, EMAIL, TEAM, team_public_key, token
from .test_http_security import INIT, KEY, MCP_HEADERS, ORIGIN, rpc

ADMIN = "admin@example.org"
REPORT = [DailyUsage("2026-10-22", analyses=5, high_priority=1, review=2, no_suspicious_findings=1,
                     not_a_mammogram=1, views=7, detailed_views=4, people=3)]


def make_client():
    h = handlers()
    mcp = create_mcp_server(h, McpOptions(allow_local_paths=False, max_image_bytes=100_000))
    app = create_http_app(mcp, HttpOptions(
        api_key=KEY, max_body_bytes=200_000, rate_limit_per_minute=100, allowed_origins=(ORIGIN,),
        allowed_hosts=("testserver",), access=CloudflareAccessVerifier(TEAM, AUD, key_resolver=team_public_key),
        admin_emails=(ADMIN.upper(),), usage_report=lambda days: REPORT[:days]))
    return TestClient(app), h


def analyze(client, **headers):
    call = rpc("tools/call", {"name": "mammo_analyze_mammogram",
                              "arguments": {"image_base64": base64.b64encode(PNG_BYTES).decode()}}, id_=2)
    client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **headers})
    return client.post("/mcp", json=call, headers={**MCP_HEADERS, **headers})


def test_the_access_identity_reaches_the_analysis_and_the_key_brings_none():
    client, h = make_client()
    with client:
        assert analyze(client, **{"cf-access-jwt-assertion": token()}).status_code == 200
        assert analyze(client, authorization=f"Bearer {KEY}").status_code == 200
    assert [c.requester for c in h.analyze.calls] == [EMAIL, None]


def test_usage_needs_credentials():
    client, _ = make_client()
    with client:
        assert client.get("/usage").status_code == 401


def test_usage_is_forbidden_to_everyone_but_the_administrator():
    client, _ = make_client()
    with client:
        assert client.get("/usage", headers={"cf-access-jwt-assertion": token()}).status_code == 403
        assert client.get("/usage", headers={"authorization": f"Bearer {KEY}"}).status_code == 403


def test_the_administrator_sees_the_daily_counts():
    client, _ = make_client()
    with client:
        r = client.get("/usage?lang=es", headers={"cf-access-jwt-assertion": token(email=ADMIN)})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "Uso del piloto" in r.text and "<td>2026-10-22</td><td>5</td><td>3</td>" in r.text
