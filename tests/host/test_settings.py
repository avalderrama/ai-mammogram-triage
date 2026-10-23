"""Startup refuses unsafe HTTP configurations."""
import jwt
import pytest
from starlette.testclient import TestClient

from mammo_host.composition import build_http_app
from mammo_host.settings import Settings
from mammo_infrastructure import Ports
from presentation.test_access_auth import AUD, TEAM, FakeJwksClient, token
from presentation.test_http_security import INIT, MCP_HEADERS


def settings(**kw):
    return Settings(models_dir="unused", _env_file=None, **kw)


@pytest.mark.parametrize("key", [None, "short"])
def test_missing_or_short_key_exits_cleanly(key):
    with pytest.raises(SystemExit, match="24 characters"):
        build_http_app(settings(api_key=key))


def test_public_host_requires_trusting_the_tunnel_header():
    with pytest.raises(SystemExit, match="TRUST_PROXY_HEADERS"):
        build_http_app(settings(api_key="k" * 32, allowed_hosts=("127.0.0.1:*", "abc.trycloudflare.com")))


def test_csv_lists_are_parsed():
    s = settings(allowed_hosts="a.example, 127.0.0.1:*")
    assert s.allowed_hosts == ("a.example", "127.0.0.1:*") and s.exposed_beyond_loopback


def test_admin_emails_are_a_lowercase_list_and_usage_is_off_by_default():
    s = settings(admin_emails="Admin@Example.org, second@example.org")
    assert s.admin_emails == ("admin@example.org", "second@example.org")
    assert s.usage_file is None and s.usage_timezone == "America/Bogota"


@pytest.mark.parametrize("hosts", [("[::1]",), ("[::1]:*",), ("::1",), ("localhost:*", "127.0.0.1")])
def test_loopback_hosts_are_not_public(hosts):
    assert not settings(allowed_hosts=hosts).exposed_beyond_loopback


@pytest.mark.parametrize("access", [{"access_team_domain": "team.cloudflareaccess.com"}, {"access_aud": "a" * 64}])
def test_access_needs_both_the_team_and_the_audience(access):
    with pytest.raises(SystemExit, match="together"):
        build_http_app(settings(api_key="k" * 32, **access))


@pytest.mark.parametrize("value", ["https://Team.CloudflareAccess.com/", " team.cloudflareaccess.com "])
def test_access_team_domain_is_normalized_to_a_host(value):
    s = settings(access_team_domain=value, access_aud="a" * 64)
    assert s.access_team_domain == "team.cloudflareaccess.com" and s.access_configured


@pytest.mark.parametrize("value", ["team.cloudflareaccess.com/path", "http://team.example", "no_dots"])
def test_access_team_domain_rejects_anything_but_a_host(value):
    with pytest.raises(ValueError, match="host name"):
        settings(access_team_domain=value, access_aud="a" * 64)


def test_blank_access_values_mean_not_configured():
    assert not settings(access_team_domain=" ", access_aud="").access_configured


def test_both_access_settings_wire_the_verifier_into_the_http_app(monkeypatch):
    monkeypatch.setattr(jwt, "PyJWKClient", FakeJwksClient)
    app = build_http_app(settings(api_key="k" * 32, allowed_hosts=("testserver",), trust_proxy_headers=True,
                                  access_team_domain=TEAM, access_aud=AUD), ports=Ports(*[None] * 6))
    with TestClient(app) as client:
        r = client.post("/mcp", json=INIT, headers={**MCP_HEADERS, "cf-access-jwt-assertion": token()})
    assert r.status_code == 200
