# ADR 0006 — Cloudflare Access identity instead of the key for doctors

- **Status:** accepted (2026-10-21)
- **Builds on:** [ADR 0004](0004-local-pilot-with-tunnel-and-key.md)

## Context

The pilot moved to a VM with a named tunnel, and Cloudflare Access now protects `app.mammotriage.com`: doctors sign
in with their e-mail and a one-time code, and the session lasts a week. The shared key was still required on `/mcp`,
and the app keeps it only in memory, so doctors had to paste it every time they opened the app. Two barriers, and
the second one was the uncomfortable one.

Access adds a signed JWT (`Cf-Access-Jwt-Assertion`) to every request it lets through. Verifying it on the server
identifies each doctor without a key.

## Decision

- The server accepts **either** the key **or** a valid Access token. The key stays for technical clients (Claude
  Desktop, tests, scripts).
- A token is valid only with an RS256 signature from the team's keys (fetched from
  `https://<team>/cdn-cgi/access/certs`, cached and refetched when a new key id appears), the team as issuer, the
  application's AUD tag as audience, and `exp`/`iat` within 30 s of clock skew. `nbf` is checked when present.
- Only the header is read, not the `CF_Authorization` cookie: Access sends the same token in both, and the signature
  is what gives trust, not the channel.
- It is enabled with `MAMMO_ACCESS_TEAM_DOMAIN` and `MAMMO_ACCESS_AUD`, both or neither (checked at startup).
  Without them the server behaves as before.
- Verification lives in the presentation ring (`mammo_api/access.py`), next to the key check: authentication is
  resolved at the edge (`CA-PRE-005`).
- In the app, the key becomes optional. When Access redirects a request because the session expired, the app shows
  "Your session has expired" with a **Reload the page** button instead of a generic error.

## Consequences

- Doctors sign in once a week with their e-mail and never see the key.
- Each doctor has their own identity: removing one from the Access policy does not affect the others.
- If the team's keys cannot be fetched, keys already seen keep working until the endpoint answers again, so a
  rotated key may be accepted during that outage; new key ids get 401 (fail closed) and fetches wait 30 s before
  retrying. The API key keeps working. Requests already waiting for the first failed fetch still fetch one after
  another (5 s each at most), bounded by the rate limit.
- `cloudflared` can check the Access token too (`originRequest.access` in the tunnel's `config.yml`): a second check,
  independent of the server's.
- A session already open lasts until it expires, even after removing the doctor from the policy. Revoking the
  user's sessions in Zero Trust cuts it at once.
- New dependency: `PyJWT[crypto]`.
