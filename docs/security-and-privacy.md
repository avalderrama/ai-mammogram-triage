# Security and privacy

This document explains how the server protects patients and itself, and which risks remain open.

## Principles

1. **No image is stored.** Each image is processed in memory and discarded. The server does not write images to
   disk or log them.
2. **Personal data is masked before analysis.** OCR masks the text drawn over the image. The text it reads is never
   returned or logged.
3. **Remote access requires a key** and goes only over HTTPS, through a tunnel. No router ports are opened.
4. **Weights and training data are not published.** The PhysioNet data use agreement for VinDr-Mammo requires this.

## Implemented defenses

| Risk | Defense | Where |
|---|---|---|
| Unauthorized access | Required key of ≥ 24 characters (`Authorization: Bearer` or `X-API-Key`), compared in constant time. Without a key, the server does not start | `mammo_api/app.py`, `mammo_host/composition.py` |
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
| Tampered weights | The SHA-256 manifest requires every file, and weights are loaded with `torch.load(weights_only=True)`, so no code runs | `bundle.py`, `ml/networks.py` |
| Secrets in the repository | `.env` is ignored; `.gitignore` excludes weights and images | `.gitignore` |

There were two rounds of security review. The findings and their fixes are in the project's session report.

## Accepted risks

- **Shared key.** If it leaks, anyone can use the service. Mitigations:
  - rotate it with `mammo-triage new-key`;
  - for anything beyond a short trial, put Cloudflare Access in front of the tunnel.
- **OCR is not perfect.** It may miss very faint or very large text.
- **EXIF metadata.** Photos arrive with it (device, date, sometimes GPS). The server does not store it.
- **Python dependencies with version ranges.** For a reproducible deployment, generate a lock file with *hashes*.
- **Not a medical device.** It has no regulatory approval and no clinical validation.

## What to do if the key leaks

1. Stop the server (`Ctrl+C`).
2. Generate a new key with `mammo-triage new-key` and put it in `.env`.
3. Start again with `scripts/start_pilot.ps1`. The tunnel addresses change too.
4. Share the new key through a private channel.
