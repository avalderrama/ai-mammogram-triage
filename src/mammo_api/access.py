"""Cloudflare Access identity: verifies the JWT that Access adds to every request it lets through.

Access sends it in the `Cf-Access-Jwt-Assertion` header. A token is trusted only when it is signed with one of the
team's keys, was issued by the team and is meant for this Access application; anyone who reaches the origin without
going through Access cannot forge it.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import jwt

log = logging.getLogger(__name__)

ACCESS_JWT_HEADER = b"cf-access-jwt-assertion"
_ALGORITHMS = ["RS256"]
_CLOCK_SKEW_SECONDS = 30
_MAX_TOKEN_CHARS = 8192
_KEYS_LIFESPAN_SECONDS = 3600
_KEYS_TIMEOUT_SECONDS = 5
_RETRY_AFTER_FAILURE_SECONDS = 30

KeyResolver = Callable[[str], Any]
"""Returns the public key that signed a token (looked up by the token's key id)."""


class TeamKeys:
    """The team's signing keys from its certs endpoint.

    The key set is cached and refetched when an unknown key id appears (rotation), at most once per PyJWKClient
    cooldown. If a fetch fails, keys already seen keep working and new fetches wait `_RETRY_AFTER_FAILURE_SECONDS`,
    so an outage of the endpoint does not queue every request behind a slow fetch.
    """

    def __init__(self, certs_url: str, client: Any = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._client = client or jwt.PyJWKClient(certs_url, cache_jwk_set=True, lifespan=_KEYS_LIFESPAN_SECONDS,
                                                 timeout=_KEYS_TIMEOUT_SECONDS)
        self._clock = clock
        self._seen: dict[str, Any] = {}  # bounded by the team's key set: only kids the endpoint returned
        self._failed_at: float | None = None

    def __call__(self, token: str) -> Any:
        kid = jwt.get_unverified_header(token).get("kid")
        if self._failed_at is not None and self._clock() - self._failed_at < _RETRY_AFTER_FAILURE_SECONDS:
            return self._known_or_unavailable(kid)
        try:
            key = self._client.get_signing_key_from_jwt(token).key
        except (jwt.PyJWKClientConnectionError, ValueError, OSError):
            # The endpoint is unreachable or answered something that is not a key set (e.g. an error page with 200).
            # An unknown key id (PyJWKClientError) is not caught: that token must be rejected.
            self._failed_at = self._clock()
            return self._known_or_unavailable(kid)
        self._failed_at = None
        if isinstance(kid, str):
            self._seen[kid] = key
        return key

    def _known_or_unavailable(self, kid: object) -> Any:
        if isinstance(kid, str) and kid in self._seen:
            return self._seen[kid]
        raise jwt.PyJWKClientConnectionError("The Cloudflare Access signing keys are unavailable.")


class CloudflareAccessVerifier:
    """Accepts a token only if it has a valid RS256 signature from the team, the team as issuer, this application's
    AUD tag as audience and is within its validity window. Never logs the token or the identity inside it."""

    def __init__(self, team_domain: str, audience: str, key_resolver: KeyResolver | None = None) -> None:
        self._issuer = f"https://{team_domain}"
        self._audience = audience
        self._key_for = key_resolver or TeamKeys(f"{self._issuer}/cdn-cgi/access/certs")

    def verify(self, token: str) -> bool:
        return self.identify(token) is not None

    def identify(self, token: str) -> str | None:
        """The identity in a valid token (the e-mail, or the subject for service tokens), or None if invalid."""
        if not token or len(token) > _MAX_TOKEN_CHARS:
            return None
        try:
            claims = jwt.decode(token, self._key_for(token), algorithms=_ALGORITHMS, audience=self._audience,
                                issuer=self._issuer, leeway=_CLOCK_SKEW_SECONDS,
                                options={"require": ["exp", "iat", "iss", "aud"]})
        except jwt.PyJWKClientConnectionError as error:
            log.warning("Could not fetch the Cloudflare Access signing keys (%s).", type(error).__name__)
            return None
        except jwt.PyJWTError as error:
            log.info("Rejected a Cloudflare Access token (%s).", type(error).__name__)
            return None
        except Exception as error:  # e.g. a key set that is not JSON: deny instead of failing with a 500
            log.warning("Could not verify a Cloudflare Access token (%s).", type(error).__name__)
            return None
        identity = claims.get("email") or claims.get("sub")
        return identity.strip().lower() if isinstance(identity, str) and identity.strip() else "unknown"
