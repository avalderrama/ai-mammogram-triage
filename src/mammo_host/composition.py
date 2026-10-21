"""Composition root: the only place that knows every ring and wires them together (CA-CFG-001)."""
from __future__ import annotations

from mammo_api import CloudflareAccessVerifier, HttpOptions, create_http_app
from mammo_application.analyses import (AnalyzeMammogramHandler, CheckIsMammogramHandler,
                                        MaskPersonalDataHandler)
from mammo_infrastructure import Ports, create_ports
from mammo_mcp import McpHandlers, McpOptions, create_mcp_server

from .settings import Settings

VERSION = "0.1.0"


def build_handlers(ports: Ports, settings: Settings) -> McpHandlers:
    limit = settings.max_image_bytes
    return McpHandlers(
        analyze=AnalyzeMammogramHandler(ports.masker, ports.splitter, ports.gate, ports.classifier, ports.fuser,
                                        ports.thresholds, limit),
        check=CheckIsMammogramHandler(ports.splitter, ports.gate, ports.thresholds, limit),
        mask=MaskPersonalDataHandler(ports.masker, limit),
    )


def build_mcp(settings: Settings, allow_local_paths: bool, ports: Ports | None = None):
    ports = ports or create_ports(settings.models_dir, settings.device, settings.verify_bundle)
    return create_mcp_server(build_handlers(ports, settings),
                             McpOptions(allow_local_paths, settings.max_image_bytes, VERSION))


def build_http_app(settings: Settings, ports: Ports | None = None):
    if settings.api_key is None or len(settings.api_key.get_secret_value()) < 24:
        raise SystemExit("MAMMO_API_KEY is required for the http transport and needs at least 24 characters "
                         "(generate one with: mammo-triage new-key).")
    if settings.exposed_beyond_loopback and not settings.trust_proxy_headers:
        raise SystemExit("MAMMO_ALLOWED_HOSTS includes a public host (tunnel) but MAMMO_TRUST_PROXY_HEADERS is false: "
                         "every user would share one rate-limit bucket. Set MAMMO_TRUST_PROXY_HEADERS=true.")
    if (settings.access_team_domain is None) != (settings.access_aud is None):
        raise SystemExit("Set MAMMO_ACCESS_TEAM_DOMAIN and MAMMO_ACCESS_AUD together (Cloudflare Access), or neither.")
    access = (CloudflareAccessVerifier(settings.access_team_domain, settings.access_aud)
              if settings.access_configured else None)
    mcp = build_mcp(settings, allow_local_paths=False, ports=ports)
    return create_http_app(mcp, HttpOptions(
        api_key=settings.api_key.get_secret_value(), max_body_bytes=settings.max_body_bytes,
        rate_limit_per_minute=settings.rate_limit_per_minute, allowed_origins=settings.allowed_origins,
        allowed_hosts=settings.allowed_hosts, trust_proxy_headers=settings.trust_proxy_headers, access=access))
