# Security and privacy

This document explains how the server protects patients and itself, and which risks remain open.

## Principles

1. **No image is stored.** Each image is processed in memory and discarded. The server does not write images to
   disk or log them.
2. **Personal data is masked before analysis.** OCR masks the text drawn over the image. The text it reads is never
   returned or logged.
3. **Remote access requires an identity**: the key, or a Cloudflare Access sign-in verified by the server. It goes
   only over HTTPS, through a tunnel. No router ports are opened.
4. **Weights and training data are not published.** The PhysioNet data use agreement for VinDr-Mammo requires this.

## Implemented defenses

| Risk | Defense | Where |
|---|---|---|
| Unauthorized access | Required key of ≥ 24 characters (`Authorization: Bearer` or `X-API-Key`), compared in constant time. Without a key, the server does not start | `mammo_api/app.py`, `mammo_host/composition.py` |
| Browsers without a key | With `MAMMO_ACCESS_TEAM_DOMAIN` and `MAMMO_ACCESS_AUD`, a Cloudflare Access token (`Cf-Access-Jwt-Assertion`) replaces the key. It must have an RS256 signature from the team's keys (cached, refetched on rotation), the team as issuer, the application's AUD tag as audience, and `exp`/`iat` within 30 s of clock skew. Anything else is 401, including any error while verifying. If the keys cannot be fetched, keys already seen keep working, unknown ones fail closed and fetches wait 30 s before retrying. The token and the e-mail inside it are never logged | `mammo_api/access.py`, `AuthenticationMiddleware` |
| Clickjacking and missing headers | nginx adds `frame-ancestors 'none'`, `X-Frame-Options`, `nosniff`, `Referrer-Policy` and `Permissions-Policy` to every response, the server's included | `deploy/azure-vm/nginx-mammotriage.conf` |
| Brute force or abuse | Per-client, per-minute limit on `/mcp`, which also counts rejected requests. At most 3 analyses in flight (`server_busy`) | `RateLimitMiddleware`, `mammo_mcp/server.py` |
| Spoofed IP behind the tunnel | `CF-Connecting-IP` is accepted only if the connection comes from the machine itself. The server does not start with a public host unless `MAMMO_TRUST_PROXY_HEADERS` is set | `RateLimitMiddleware`, `composition.py` |
| Huge requests | Body limit by `Content-Length` and by chunks (413); base64 limit before decoding | `BodyLimitMiddleware`, `mammo_mcp/inputs.py` |
| Decompression bombs | At most 60 MP, checked before decoding; Pillow warnings are treated as errors | `ml/imaging.py` |
| Unexpected formats | The format is detected from its content, the declared type is ignored, and only 5 decoders are reachable | `inputs.py`, `imaging.py` |
| Reading server files | `image_path` exists only in stdio (local use) | `inputs.py` |
| SSRF | The server does not accept image URLs, only base64 | Design |
| CSRF and *DNS rebinding* | CORS limited to the app's origins; `Host` and `Origin` validated | `TransportSecuritySettings`, `CORSMiddleware` |
| `/health` as a back door | Only `GET` and `HEAD` are public; any other method requires the key | `_is_health_probe` |
| Information leaks in errors | Unexpected errors return a generic `internal_error`; the traceback goes only to the log, without the arguments. The access log is off | `mammo_mcp/server.py`, `__main__.py` |
| Tampered weights | The SHA-256 manifest requires every file of the bundle's format. ONNX files are a graph of operators with no code; PyTorch weights are loaded with `torch.load(weights_only=True)`. Either way, no code from the bundle runs | `bundle.py`, `ml/onnx_backend.py`, `ml/torch_backend.py` |
| Secrets in the repository | `.env` is ignored; `.gitignore` excludes weights and images | `.gitignore` |
| Usage statistics revealing people | The usage counters keep only numbers per day: analyses, results by level, rejections, errors, views and distinct people. A person is counted once a day through an HMAC of the day and their e-mail, keyed with the API key; those hashes exist only for the current day and are dropped once it is over (at the next analysis, view of `/usage` or restart), so only the count remains; service tokens are not counted as people. Someone holding both the API key and the counters file could test whether a given e-mail analyzed today; the file is readable only by the service (mode 0700 folder). No image, result detail, e-mail or IP is stored. `/usage` answers only the e-mails in `MAMMO_ADMIN_EMAILS` (403 for anyone else, also with the API key) | `mammo_infrastructure/usage_log.py`, `mammo_api/app.py` |

There were two rounds of security review. The findings and their fixes are in the project's session report.

## Accepted risks

- **Shared key.** If it leaks, anyone can use the service. Mitigations:
  - rotate it with `mammo-triage new-key`;
  - with Cloudflare Access configured, doctors do not need it: only technical clients use it, so it rarely
    travels.
- **Access tokens are trusted for their whole lifetime.** Removing a doctor from the Access policy blocks new
  sign-ins at once; a session already open lasts until it expires (1 week in the pilot). To cut it at once, revoke
  the user's sessions in Cloudflare Zero Trust.
- **OCR is not perfect.** It may miss very faint or very large text. On fake personal data drawn over 24
  mammograms, RapidOCR (ONNX bundle) hid 60 of 60 lines and EasyOCR (PyTorch bundle) 58 of 60. RapidOCR reads large
  images in full-resolution tiles: on a 12 MP image it hid text down to 0.3 % of the image height. On real figures it
  sometimes misses short view labels such as "LMLO" or a panel letter.
- **EXIF metadata.** Photos arrive with it (device, date, sometimes GPS). The server does not store it.
- **Python dependencies with version ranges.** For a reproducible deployment, generate a lock file with *hashes*.
- **Not a medical device.** It has no regulatory approval and no clinical validation.

## What to do if the key leaks

1. Stop the server (`Ctrl+C`).
2. Generate a new key with `mammo-triage new-key` and put it in `.env`.
3. Start again with `scripts/start_pilot.ps1`. The tunnel addresses change too.
4. Share the new key through a private channel.
