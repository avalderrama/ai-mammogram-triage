# ai-mammogram-triage

An **MCP** server for indicative triage of mammograms (CC/MLO views). It sorts each breast into three BI-RADS groups
(1-2, 3 and 4-5) and gives it a priority level:

| Level | Meaning | Calibration criterion |
|---|---|---|
| `high_priority` | Read first | ≈5 % false alarms (calibration target; 6.5 % of BI-RADS 1-2 breasts in the held-out half B) |
| `review` | Look carefully | Together with `high_priority`, flags 85 % of BI-RADS 4-5 breasts (calibration target; 87 % in half B) |
| `no_suspicious_findings` | The model sees no suspicious findings | — |

> ⚠️ **Research tool.** It is not a medical device, it is not clinically validated and it never replaces the
> radiologist's reading.

It is part of a three-repository project:

| Repository | Role |
|---|---|
| `ai-mammogram-triage-training` (private) | Data, training, experiments and calibration. Exports the model bundle |
| **`ai-mammogram-triage`** (this one) | MCP server that analyzes images with that bundle |
| [`ai-mammogram-triage-app`](https://github.com/avalderrama/ai-mammogram-triage-app) | Web app (PWA) for doctors that uses this server |

## What each analysis does

1. **Masks personal data** drawn over the image, using OCR. The text it reads is never returned or logged.
2. **Checks that the image is a mammogram.** It rejects ultrasound, MRI and unrelated photos, and warns about photo
   quality problems.
3. **Classifies each view** with two global models (512 and 1024 px). If the image is at least 1500 px high, it adds
   a **2048 px patch model** (better with calcifications), a **heatmap** and up to 3 **suspicious regions** per view
   (where the patch model looks most; see [docs/scores-and-levels.md](docs/scores-and-levels.md)).
4. **Combines views of the same breast** (for example RCC + RMLO) **only when the client gives `sides`** (R/L).
   There is no automatic side detection. Without `sides`, each view is evaluated as its own "breast", labelled
   "panel N". Then it applies the calibrated thresholds.

Images are **never stored**.

## Summary language

All three tools accept `language: "en" | "es"` (default `"en"`). It sets the language of the human-readable text
summary only, for example "HIGH PRIORITY", "REVIEW" or "NO SUSPICIOUS FINDINGS" in English. `structuredContent` is
always in English.

## Performance

Measured **per breast** on the half of the patients in the official VinDr-Mammo test split that was not used for
calibration (half B):

| Mode | AUC BI-RADS 4-5 | 4-5 → high / review / no findings | 1-2 → high / review / no findings |
|---|---|---|---|
| Global + patches (≥ 1500 px) | 0.89 | 70 % / 17 % / 13 % | 7 % / 21 % / 72 % |
| Global only (low resolution) | 0.83 | 53 % / 30 % / 17 % | 3 % / 29 % / 68 % |

Photos of screens or films perform worse than the original file. How these figures are computed and how to read
them: [docs/scores-and-levels.md](docs/scores-and-levels.md).

## Quick start

Requires Python 3.11 and, optionally, an NVIDIA GPU.

```bash
pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cu126
```

```bash
pip install -e ".[dev]"
```

The weights are **not published** (see [ADR 0003](docs/adr/0003-weights-not-published.md)). Set
`MAMMO_MODELS_DIR` to the folder of the bundle exported by the training repository.

**Local, with Claude Desktop (stdio):**

```json
{
  "mcpServers": {
    "mammo-triage": {
      "command": "C:\\path\\to\\venv\\Scripts\\mammo-triage.exe",
      "args": ["stdio"],
      "env": { "MAMMO_MODELS_DIR": "C:\\path\\to\\bundle\\v1" }
    }
  }
}
```

**Over HTTP (for the app or a tunnel):** generate a key, put it in `.env` (copied from `.env.example`) and start
the server.

```bash
mammo-triage new-key
```

```bash
mammo-triage http
```

The server listens on `http://127.0.0.1:8765/mcp`.

**Pilot with doctors (Cloudflare HTTPS tunnels)**, in PowerShell:

```powershell
.\scripts\start_pilot.ps1
```

If the server is installed in a virtual environment other than the default one, pass `-Python`. All parameters
are listed in [docs/operations.md](docs/operations.md#remote-pilot-with-https-tunnels).

## Documentation

| Document | Contents |
|---|---|
| [Architecture](docs/architecture.md) | Three-repository system, layers, path of an analysis, deployment and components, with diagrams |
| [Scores and levels](docs/scores-and-levels.md) | How the result is computed and explained; thresholds and performance |
| [MCP tools](docs/mcp-tools.md) | Reference for arguments, responses and error codes |
| [Security and privacy](docs/security-and-privacy.md) | Defenses, accepted risks and what to do if the key leaks |
| [Operations](docs/operations.md) | Installation, configuration variables, start modes, pilot and common problems |
| [Development](docs/development.md) | Tests, conventions and how to make the usual changes |
| [Decisions (ADR)](docs/adr/) | Python, MCP without REST, unpublished weights, pilot with tunnel and key |

## Tests

Regular tests, and integration tests with the real models (they need the bundle and a local image library):

```bash
pytest
```

```bash
pytest -m integration
```

## Known limitations

- Trained mostly on Vietnamese mammograms (VinDr-Mammo). Performance on other populations and equipment is not
  validated.
- The filter accepts tomosynthesis slices as if they were 2D mammograms.
- Low-resolution photos use only the global model, with no heatmap.
- OCR may miss very faint or very large text.
