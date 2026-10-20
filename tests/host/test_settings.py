"""Startup refuses unsafe HTTP configurations."""
import pytest

from mammo_host.composition import build_http_app
from mammo_host.settings import Settings


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


@pytest.mark.parametrize("hosts", [("[::1]",), ("[::1]:*",), ("::1",), ("localhost:*", "127.0.0.1")])
def test_loopback_hosts_are_not_public(hosts):
    assert not settings(allowed_hosts=hosts).exposed_beyond_loopback
