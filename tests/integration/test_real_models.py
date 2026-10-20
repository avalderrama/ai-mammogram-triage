"""End-to-end through HTTP + MCP with the real bundle. Run with `pytest -m integration`.

Needs MAMMO_MODELS_DIR (exported bundle) and MAMMO_TEST_IMAGES_DIR (local test library, never committed).
"""
import base64
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

pytestmark = pytest.mark.integration

MODELS = os.environ.get("MAMMO_MODELS_DIR")
IMAGES = Path(os.environ.get("MAMMO_TEST_IMAGES_DIR", "")) if os.environ.get("MAMMO_TEST_IMAGES_DIR") else None
KEY = "integration-test-key-0123456789"
HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json",
           "mcp-protocol-version": "2025-06-18", "x-api-key": KEY}

if not MODELS or IMAGES is None:
    pytest.skip("MAMMO_MODELS_DIR and MAMMO_TEST_IMAGES_DIR are required", allow_module_level=True)


@pytest.fixture(scope="module")
def client():
    os.environ["MAMMO_API_KEY"] = KEY
    from mammo_host.composition import build_http_app
    from mammo_host.settings import Settings
    settings = Settings(allowed_hosts=("testserver",), trust_proxy_headers=True, rate_limit_per_minute=0)
    with TestClient(build_http_app(settings)) as c:
        yield c


def call(client, tool, path: Path, **extra):
    data = base64.b64encode(path.read_bytes()).decode()
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": {"image_base64": data, **extra}}}
    r = client.post("/mcp", json=body, headers=HEADERS)
    assert r.status_code == 200
    return r.json()["result"]


def test_mass_is_high_priority(client):
    result = call(client, "mammo_analyze_mammogram", IMAGES / "imagenes" / "rmlo_masa.png", sides=["R"],
                  views=["MLO"])
    assert not result["isError"]
    assert result["structuredContent"]["breasts"][0]["level"] == "high_priority"


def test_ultrasound_is_not_a_mammogram(client):
    result = call(client, "mammo_analyze_mammogram", IMAGES / "no_mamografia" / "ecografia_mama_sin_datos.png")
    assert result["isError"] and result["structuredContent"]["error"] == "not_a_mammogram"


def test_check_tool_accepts_mammogram(client):
    result = call(client, "mammo_check_is_mammogram", IMAGES / "imagenes" / "rcc_masa.png")
    assert result["structuredContent"]["panels"][0]["is_mammogram"] is True
