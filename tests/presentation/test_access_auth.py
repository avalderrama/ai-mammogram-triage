"""Cloudflare Access identity: a signed token from the team replaces the API key; anything else is 401."""
import logging
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from mammo_api import CloudflareAccessVerifier, HttpOptions, create_http_app
from mammo_api.access import TeamKeys
from mammo_mcp import McpOptions, create_mcp_server

from .fakes import handlers
from .test_http_security import INIT, KEY, MCP_HEADERS, ORIGIN

TEAM = "team.cloudflareaccess.com"
AUD = "a" * 64
EMAIL = "doctor@example.org"
TEAM_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(signing_key=TEAM_KEY, algorithm="RS256", drop=(), **overrides):
    now = int(time.time())
    claims = {"aud": [AUD], "iss": f"https://{TEAM}", "email": EMAIL, "sub": "user-id", "iat": now, "nbf": now,
              "exp": now + 3600, **overrides}
    for name in drop:
        del claims[name]
    return jwt.encode(claims, signing_key, algorithm=algorithm)


def team_public_key(_token):
    return TEAM_KEY.public_key()


def make_client(access=True, resolver=team_public_key):
    mcp = create_mcp_server(handlers(), McpOptions(allow_local_paths=False, max_image_bytes=100_000))
    verifier = CloudflareAccessVerifier(TEAM, AUD, key_resolver=resolver) if access else None
    app = create_http_app(mcp, HttpOptions(api_key=KEY, max_body_bytes=200_000, rate_limit_per_minute=100,
                                           allowed_origins=(ORIGIN,), allowed_hosts=("testserver",),
                                           access=verifier))
    return TestClient(app)


def post_init(client, **headers):
    return client.post("/mcp", json=INIT, headers={**MCP_HEADERS, **headers})


def test_valid_access_token_replaces_the_api_key():
    with make_client() as c:
        r = post_init(c, **{"cf-access-jwt-assertion": token()})
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "mammogram_triage_mcp"


def test_api_key_still_works_when_access_is_configured():
    with make_client() as c:
        assert post_init(c, authorization=f"Bearer {KEY}").status_code == 200


@pytest.mark.parametrize("bad", [
    token(aud=["another-application"]),
    token(iss="https://other-team.cloudflareaccess.com"),
    token(exp=int(time.time()) - 3600, iat=int(time.time()) - 7200, nbf=int(time.time()) - 7200),
    token(nbf=int(time.time()) + 3600),
    token(drop=("exp",)),
    token(drop=("iat",)),
    token(signing_key=OTHER_KEY),
    token(signing_key="a-shared-secret-that-is-long-enough-for-hs256", algorithm="HS256"),
    "not-a-jwt",
    "x" * 9000,
], ids=["audience", "issuer", "expired", "not-yet-valid", "no-exp", "no-iat", "foreign-signature", "hs256",
        "garbage", "oversized"])
def test_invalid_access_tokens_are_401(bad):
    with make_client() as c:
        r = post_init(c, **{"cf-access-jwt-assertion": bad})
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")


def test_access_header_is_ignored_when_access_is_not_configured():
    with make_client(access=False) as c:
        assert post_init(c, **{"cf-access-jwt-assertion": token()}).status_code == 401


def test_unreachable_signing_keys_fail_closed_without_logging_the_token(caplog):
    def unreachable(_token):
        raise jwt.PyJWKClientConnectionError("network down")

    bearer = token()
    with caplog.at_level(logging.INFO), make_client(resolver=unreachable) as c:
        r = post_init(c, **{"cf-access-jwt-assertion": bearer})
    assert r.status_code == 401
    assert "signing keys" in caplog.text
    assert bearer not in caplog.text and EMAIL not in caplog.text


def test_rejections_never_log_the_token_or_the_email(caplog):
    bad = token(aud=["another-application"])
    with caplog.at_level(logging.DEBUG), make_client() as c:
        post_init(c, **{"cf-access-jwt-assertion": bad})
    assert "Rejected a Cloudflare Access token" in caplog.text
    assert bad not in caplog.text and EMAIL not in caplog.text


class FakeJwksClient:
    """Stands in for PyJWKClient: records the certs URL and serves the team's public key."""
    urls: list[str] = []

    def __init__(self, uri, **_options):
        FakeJwksClient.urls.append(uri)

    def get_signing_key_from_jwt(self, _token):
        return SimpleNamespace(key=TEAM_KEY.public_key())


def test_default_key_resolver_reads_the_team_certs_endpoint(monkeypatch):
    monkeypatch.setattr(jwt, "PyJWKClient", FakeJwksClient)
    verifier = CloudflareAccessVerifier(TEAM, AUD)
    assert FakeJwksClient.urls[-1] == f"https://{TEAM}/cdn-cgi/access/certs"
    assert verifier.verify(token())


@pytest.mark.parametrize("error", [ValueError("not JSON"), OSError("connection reset")])
def test_unexpected_key_errors_deny_instead_of_crashing(error, caplog):
    def broken(_token):
        raise error

    with caplog.at_level(logging.WARNING), make_client(resolver=broken) as c:
        r = post_init(c, **{"cf-access-jwt-assertion": token()})
    assert r.status_code == 401 and "Could not verify" in caplog.text


class FlakyClient:
    """A PyJWKClient whose fetches fail on demand."""

    def __init__(self):
        self.calls, self.down = 0, False

    def get_signing_key_from_jwt(self, _token):
        self.calls += 1
        if self.down:
            raise jwt.PyJWKClientConnectionError("down")
        return SimpleNamespace(key=TEAM_KEY.public_key())


def kid_token(kid):
    now = int(time.time())
    return jwt.encode({"aud": [AUD], "iss": f"https://{TEAM}", "iat": now, "exp": now + 60}, TEAM_KEY,
                      algorithm="RS256", headers={"kid": kid})


def test_team_keys_keep_known_keys_and_back_off_while_the_endpoint_is_down():
    now = [0.0]
    client = FlakyClient()
    keys = TeamKeys("https://unused", client=client, clock=lambda: now[0])
    assert keys(kid_token("k1")) is not None and client.calls == 1

    client.down = True
    assert keys(kid_token("k1")) is not None          # known key keeps working through the outage
    with pytest.raises(jwt.PyJWKClientConnectionError):
        keys(kid_token("k2"))                         # unknown key: fails fast during the back-off...
    assert client.calls == 2                          # ...without another fetch

    now[0] = 31.0
    client.down = False
    assert keys(kid_token("k2")) is not None and client.calls == 3   # retried after the back-off


def test_team_keys_back_off_when_the_endpoint_answers_garbage():
    class GarbageClient(FlakyClient):
        def get_signing_key_from_jwt(self, token):
            if self.down:
                self.calls += 1
                raise ValueError("Expecting value: line 1 column 1")  # JSONDecodeError is a ValueError
            return super().get_signing_key_from_jwt(token)

    client = GarbageClient()
    keys = TeamKeys("https://unused", client=client, clock=lambda: 0.0)
    keys(kid_token("k1"))
    client.down = True
    assert keys(kid_token("k1")) is not None and client.calls == 2
    assert keys(kid_token("k1")) is not None and client.calls == 2   # backing off: no refetch
