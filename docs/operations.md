# Operations: install, configure, start and troubleshoot

This document explains how to get the server running, how to configure it, how to start the pilot and what to do
when something fails.

## 1. Requirements

- Windows 10/11 or Linux, with Python 3.11.
- The **model bundle** (~65 MB) exported by the training repository, in one of the two formats below.
- For the remote pilot, `cloudflared` (Cloudflare's official executable). Verify its SHA-256 and its signature when
  you download it.

Both bundle formats give the same results ([ADR 0005](adr/0005-onnx-runtime-backend.md)). Measured with the full
HTTP + MCP app on a 4-core CPU, over a viewer capture, a multi-view figure, two 1630×2048 views and a 12 MP photo:

| | ONNX bundle (recommended) | PyTorch bundle |
|---|---|---|
| Files | `gate.onnx`, `global_512.onnx`, `global_1024.onnx`, `patch_2048.onnx`, `onnx_meta.json` | `gate.pt`, `global_512.pt`, `global_1024.pt`, `patch_2048.pt` |
| Always | `manifest.json` (declares `"format"`), `fusion_calibration.json` | the same (no `"format"` means PyTorch) |
| Exported with | `export_bundle.py` then `export_onnx.py` | `export_bundle.py` |
| Runs on | CPU (ONNX Runtime + RapidOCR) | NVIDIA GPU with CUDA, or CPU (PyTorch + EasyOCR) |
| Startup | ~1 s | ~6 s |
| Peak memory | ~0.6 GB (the process rests at ~0.3 GB) | ~5 GB on CPU (and stays there) |
| Time per image | 1–3 s; ~7 s for a 12 MP photo (its text is read in tiles) | 1–10 s on CPU; much less with a GPU |

A server with the ONNX bundle fits in a 1 GB VM: add a 2 GB swap file as a safety margin and measure it there before
real use.

## 2. Installation

```bash
python -m venv .venv
```

**ONNX bundle:**

```bash
.venv\Scripts\pip install -e ".[onnx]"
```

RapidOCR depends on `opencv-python`, which needs `libGL` on Linux servers. On a headless server, install
`opencv-python-headless` and RapidOCR without its dependencies, or install `libgl1` with the system's package manager.
RapidOCR's models ship inside the package: nothing is downloaded at run time.

**PyTorch bundle:**

```bash
.venv\Scripts\pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu126
```

```bash
.venv\Scripts\pip install -e ".[torch]"
```

On first use, EasyOCR downloads its text detection models.

Add `dev` to the extras (`".[onnx,dev]"`) to run the tests.

## 3. Configuration (`.env` or environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `MAMMO_MODELS_DIR` | (required) | Folder of the model bundle |
| `MAMMO_API_KEY` | (required for HTTP) | Shared key, ≥ 24 characters. Generate it with `mammo-triage new-key` |
| `MAMMO_DEVICE` | `auto` | `auto`, `cuda` or `cpu`. PyTorch bundles only: ONNX bundles run on CPU and reject `cuda` |
| `MAMMO_VERIFY_BUNDLE` | `true` | Checks the SHA-256 of the weights at startup |
| `MAMMO_MAX_IMAGE_MB` | `10` | Maximum image size |
| `MAMMO_RATE_LIMIT_PER_MINUTE` | `60` | MCP requests per client per minute. One analysis costs about 3 |
| `MAMMO_ALLOWED_ORIGINS` | `http://localhost:5173` | App origins allowed by CORS, comma-separated |
| `MAMMO_ALLOWED_HOSTS` | `127.0.0.1:*,localhost:*` | Accepted `Host` values (*DNS rebinding* protection) |
| `MAMMO_TRUST_PROXY_HEADERS` | `false` | Use `CF-Connecting-IP` for the rate limit. Required with a tunnel |
| `MAMMO_HOST` / `MAMMO_PORT` | `127.0.0.1` / `8765` | Listening address. Do not change the host: use a tunnel |
| `MAMMO_LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error` |
| `MAMMO_ACCESS_TEAM_DOMAIN` | (empty) | Cloudflare Access team domain, e.g. `myteam.cloudflareaccess.com`. With `MAMMO_ACCESS_AUD`, browsers signed in through Access need no key ([ADR 0006](adr/0006-cloudflare-access-identity.md)) |
| `MAMMO_ACCESS_AUD` | (empty) | AUD tag of the Access application (Zero Trust → Access controls → Applications → *app* → Additional settings). Set both or neither |
| `MAMMO_USAGE_FILE` | (empty) | JSON file for the anonymous usage counters (writable by the server). Empty: nothing is counted |
| `MAMMO_USAGE_TIMEZONE` | `America/Bogota` | Time zone that decides where a usage day ends |
| `MAMMO_SHADOW_GATE_DIR` | (empty) | Another ONNX bundle whose gate judges every view **without deciding** (shadow mode); `/usage` counts the views where it disagrees with the gate in use. Only its gate is loaded |
| `MAMMO_ADMIN_EMAILS` | (empty) | Cloudflare Access e-mails, comma-separated, allowed to see the usage report at `/usage` |

`.env` is never committed. The template is `.env.example`.

## 4. Usage modes

### Local with Claude Desktop or Claude Code (stdio)

Add the server to the client's MCP configuration (see the [README](../README.md)). With stdio you can pass local
file paths (`image_path`).

### Local HTTP (for app development)

```bash
mammo-triage http
```

The server listens on `http://127.0.0.1:8765/mcp`, and `/health` answers `{"status":"ok"}`.

On Windows, `scripts/run_local.cmd` does the same with the settings in `.env`. It uses the Python in
`%MAMMO_PYTHON%`, or `%USERPROFILE%\.venvs\birads-poc\Scripts\python.exe` if that variable is not set.

### Remote pilot with HTTPS tunnels

In PowerShell:

```powershell
.\scripts\start_pilot.ps1
```

Parameters:

| Parameter | Default | Purpose |
|---|---|---|
| `-Cloudflared` | `%USERPROFILE%\tools\cloudflared\2026.10.0\cloudflared.exe` | Path of the `cloudflared` executable |
| `-Python` | `%USERPROFILE%\.venvs\birads-poc\Scripts\python.exe` | Python of the virtual environment where the server is installed |
| `-AppDir` | `ai-mammogram-triage-app`, sibling of this repository | Folder of the app repository (built and served with `vite preview`) |
| `-LogDir` | `%USERPROFILE%\mammo-data\logs` | Folder for the tunnel and PWA logs |

If the server is installed in another virtual environment, pass `-Python`, for example
`.\scripts\start_pilot.ps1 -Python C:\path\to\venv\Scripts\python.exe`. The script stops if `-Cloudflared` or
`-Python` does not exist.

```mermaid
sequenceDiagram
    participant S as start_pilot.ps1
    participant V as vite preview (PWA)
    participant C1 as cloudflared (app)
    participant C2 as cloudflared (MCP)
    participant M as mammo-triage http
    S->>V: npm run build + preview :4173
    S->>C1: tunnel --url :4173
    C1-->>S: https://xxx.trycloudflare.com
    S->>C2: tunnel --url :8765
    C2-->>S: https://yyy.trycloudflare.com
    S->>M: ALLOWED_HOSTS=yyy, ALLOWED_ORIGINS=xxx, TRUST_PROXY_HEADERS=true
    Note over S: Shows both URLs. Ctrl+C stops everything
```

Give the doctors:

1. The app URL.
2. The server URL (it ends in `/mcp`). They enter it in the app's **Settings** screen ("Ajustes" in Spanish) and
   press **Save and test connection**.
3. The key, **through a private channel**.

The app's routes and texts are in English by default, with a Spanish option.

Quick tunnels get a new URL on every start. A fixed URL needs a Cloudflare account and a named tunnel.

Do not leave test images in the app's `dist/` folder while the tunnel is open: everything there is public.

## 5. Common problems

The app messages below are the English texts; the Spanish option shows the equivalent text.

| Symptom | Likely cause | Fix |
|---|---|---|
| `MAMMO_API_KEY is required…` at startup | The key is missing, or shorter than 24 characters | Run `mammo-triage new-key` and put it in `.env` |
| `…TRUST_PROXY_HEADERS is false` at startup | There is a public host in `MAMMO_ALLOWED_HOSTS` | Set `MAMMO_TRUST_PROXY_HEADERS=true` |
| `Checksum mismatch for …` | Corrupt or modified bundle | Export it again from the training repository |
| `…is missing: onnx_meta.json` or `gate.onnx` | ONNX bundle copied without all its files | Copy the whole folder written by `export_onnx.py` |
| `ONNX bundles run on CPU…` | `MAMMO_DEVICE=cuda` with an ONNX bundle | Set `MAMMO_DEVICE=auto`, or use the PyTorch bundle for GPU |
| `…is in 'torch' format, but its backend is not installed` (or `'onnx'`) | The extra for the bundle's format is not installed | `pip install -e ".[torch]"` or `".[onnx]"`, as the message says |
| `onnx_meta.json … lacks the metadata of: …` | Incomplete or hand-edited ONNX bundle | Export it again with `export_onnx.py` |
| The app says "The server rejects this connection" (421) | The tunnel host is not in `MAMMO_ALLOWED_HOSTS` | Use `start_pilot.ps1`, or add it by hand |
| The app says "The server could not be reached" | Server off, old tunnel URL, or origin not allowed by CORS | Check `/health`, the URL in Settings and `MAMMO_ALLOWED_ORIGINS` |
| "The access key is not valid" (401) | Wrong or rotated key | Check Settings |
| Frequent `server_busy` | Many analyses at once (they run one at a time) | Wait a few seconds; with many doctors, consider another machine |
| Always `global_only`, no heatmap | Photos less than 1500 px high | Use the original image or closer photos |
| `start_pilot.ps1` says `Not found: …` | `cloudflared` or the Python venv is not at the default path | Pass `-Cloudflared` or `-Python` |
| The first analysis takes ~20 s | PyTorch bundle: GPU and OCR warm-up | Normal; later ones are faster. The ONNX bundle has no warm-up |

## 6. Logs

- The server writes to standard error. It does not log images, OCR text or tool arguments.
- Usage: with `MAMMO_USAGE_FILE` set, `/usage` shows analyses per day (by result, views, views with heatmap and
  distinct people) to the e-mails in `MAMMO_ADMIN_EMAILS`, signed in through Cloudflare Access; anyone else gets 403.
  `?days=90` shows up to the last 90 days with activity and `?lang=es` switches the language. See [security-and-privacy.md](security-and-privacy.md)
  for what is and is not kept.
- `start_pilot.ps1` saves the tunnel and PWA logs in `-LogDir` (by default `%USERPROFILE%\mammo-data\logs`).
