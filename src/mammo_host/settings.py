"""Configuration from environment variables (prefix MAMMO_) or a local .env file. Secrets never live in code."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAMMO_", env_file=".env", extra="ignore")

    models_dir: Path = Field(description="Folder with the exported model bundle (manifest.json + weights).")
    device: Literal["auto", "cuda", "cpu"] = Field(
        default="auto", description="PyTorch bundles only; ONNX bundles always run on CPU (cuda is rejected).")
    verify_bundle: bool = True
    api_key: SecretStr | None = Field(default=None, description="Required for the http transport (>= 24 chars).")
    max_image_mb: float = Field(default=10, gt=0, le=50)
    # Each analysis costs ~3 MCP requests (initialize, initialized, tools/call).
    rate_limit_per_minute: int = Field(default=60, ge=0)
    allowed_origins: Annotated[tuple[str, ...], NoDecode] = ("http://localhost:5173",)
    allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ("127.0.0.1:*", "localhost:*")
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    trust_proxy_headers: bool = False
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    # Cloudflare Access in front of the server: browsers authenticated by Access need no API key.
    access_team_domain: str | None = Field(
        default=None, description="Access team domain, e.g. myteam.cloudflareaccess.com. Set together with access_aud.")
    access_aud: str | None = Field(default=None, description="AUD tag of the Access application.")

    @field_validator("allowed_origins", "allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(v.strip() for v in value.split(",") if v.strip())
        return value

    @field_validator("access_team_domain", mode="before")
    @classmethod
    def _team_host(cls, value: object) -> object:
        if not isinstance(value, str) or not value.strip():
            return None
        host = value.strip().lower().removeprefix("https://").rstrip("/")
        if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
            raise ValueError("MAMMO_ACCESS_TEAM_DOMAIN must be a host name such as myteam.cloudflareaccess.com.")
        return host

    @field_validator("access_aud", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        return (value.strip() or None) if isinstance(value, str) else value

    @property
    def access_configured(self) -> bool:
        return self.access_team_domain is not None and self.access_aud is not None

    @property
    def exposed_beyond_loopback(self) -> bool:
        local = {"127.0.0.1", "localhost", "[::1]", "::1"}

        def host_only(entry: str) -> str:
            if entry.startswith("["):  # [::1] or [::1]:port
                return entry[: entry.index("]") + 1]
            return entry.rsplit(":", 1)[0] if entry.count(":") == 1 else entry
        return any(host_only(h) not in local for h in self.allowed_hosts)

    @property
    def max_image_bytes(self) -> int:
        return int(self.max_image_mb * 1_000_000)

    @property
    def max_body_bytes(self) -> int:
        # base64 grows the payload by 4/3; leave room for the JSON-RPC envelope.
        return int(self.max_image_bytes * 4 / 3) + 64_000
