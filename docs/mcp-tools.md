# MCP tools reference

The server identifies itself as `mammogram_triage_mcp` and exposes three tools. All three are read-only,
non-destructive and idempotent, and they do not call external services (MCP *annotations*).

## Common arguments

| Argument | Type | Tools | Notes |
|---|---|---|---|
| `image_base64` | string | all | The image in base64. The `data:image/…;base64,` prefix is accepted |
| `image_path` | string | all | Local path. Only with the **stdio** transport; rejected over HTTP |
| `panel_count` | integer 1–8 | analyze, check | Number of views. If omitted, separators are detected, also in a grid (white lines between rows and columns) |
| `panel_arrangement` | `side_by_side` or `grid` | analyze, check | With `panel_count`: views in one row, or in a 2 × 2 grid (`panel_count` 4). Views are numbered left to right and top to bottom |
| `response_format` | `markdown` or `json` | analyze, check | Format of the text in `content[0]`. Default `markdown` |
| `language` | `en` or `es` | all | Language of the human-readable text summary. Default `en`. `structuredContent` is always in English |

Send exactly one of `image_base64` or `image_path`.

The format is detected from the file content, not from the extension or the declared type. PNG, JPEG, WebP, TIFF
and BMP are accepted, up to `MAMMO_MAX_IMAGE_MB` (10 MB by default) and 60 megapixels.

The text summary is in English by default: for example "HIGH PRIORITY", "REVIEW" or "NO SUSPICIOUS FINDINGS".
With `language: "es"` it is in Spanish ("ALTA PRIORIDAD", "REVISAR", "SIN HALLAZGOS SOSPECHOSOS"). Only the
summary changes; field names and values in `structuredContent` stay in English.

## `mammo_analyze_mammogram`

Masks personal data, discards the views that are not mammograms and gives a level to each breast.

| Additional argument | Type | Default |
|---|---|---|
| `sides` | list of `R`, `L`, `unknown` (one per view, left to right) | `unknown` for every view |
| `views` | list of `CC`, `MLO`, `unknown` | `unknown` |
| `include_heatmap` | boolean | `true` |
| `heatmap_format` | `combined` or `layers` | `combined` |

If you send `sides` or `views`, their length must match the number of views.

There is no automatic side detection. Views are combined into one breast only when `sides` says they share a side
(for example `["R", "R"]` for RCC + RMLO). Each view with side `unknown` is evaluated as its own "breast", with
`label` `panel N`.

**Successful response**

- `content[0]`: the text summary (in `language`), or the JSON if `response_format` is `json`.
- `content[1..]`: the heatmap images, in the same order as the views with `has_heatmap: true`. With
  `heatmap_format: "combined"`, one PNG per view: the red areas drawn over the processed breast. With `"layers"`,
  two PNGs per view of the same size: the processed breast (grayscale), then the red areas alone on a transparent
  background, so a viewer can show or hide them. `structuredContent.heatmap_format` says which one was used.
- `structuredContent`: the full result, always in English.

`label` is `right`, `left` or `panel N`.

```json
{
  "overall_level": "high_priority",
  "breasts": [{
    "label": "left", "side": "L", "level": "high_priority", "scoring_mode": "fused",
    "suspicion": 0.91, "attention": 0.95, "most_likely": "BI-RADS 4-5",
    "probabilities": {"birads_1_2": 0.13, "birads_3": 0.04, "birads_4_5": 0.83},
    "quality_issues": [],
    "views": [{"panel": 1, "side": "L", "view": "MLO",
               "probabilities": {"birads_1_2": 0.13, "birads_3": 0.04, "birads_4_5": 0.83},
               "quality_issues": [], "has_heatmap": true, "heatmap_unavailable_reason": null,
               "regions": [{"x": 0.3846, "y": 0.4375, "width": 0.1538, "height": 0.125, "score": 0.87,
                            "depth": "middle", "vertical": "middle"}]}]
  }],
  "rejected_panels": [],
  "masked_text_regions": 0,
  "model_version": "1.0.0",
  "heatmap_format": "combined",
  "disclaimer": "Indicative result from a research tool…"
}
```

`regions` lists, for each view, up to 3 windows where the patch model sees the most suspicion, highest first. Each
one has a box (`x`, `y`, `width`, `height`, from 0 to 1, relative to the heatmap image: the processed breast with
the chest wall on the left), a `score` (the raw patch-model P(4-5) of that window, not calibrated: use it only to
rank) and a position in words (`depth`: `posterior`, `middle` or `anterior`; `vertical`: `upper`, `middle` or
`lower`). The list is filled whenever the patch model runs, even with `include_heatmap: false`, and is empty in
`global_only` mode. The text summary names the first region of each view ("Most suspicious area (MLO): middle
third, middle part of the breast").

What each field means: [scores-and-levels.md](scores-and-levels.md).

## `mammo_check_is_mammogram`

Tells, for each view, whether it is a usable 2D mammogram and which quality problems it has. It does not classify
BI-RADS. Use it to ask for another photo before analyzing.

```json
{"panels": [{"panel": 1, "is_mammogram": true, "mammogram_confidence": 0.98,
             "quality_issues": ["blurry"]}],
 "model_version": "1.0.0"}
```

## `mammo_mask_personal_data`

Masks all text drawn over the image (names, IDs, dates, ages…) and returns a PNG. The text it reads is **never**
returned or logged; only counts by category are reported.

```json
{"masked_regions": 3, "region_categories": {"date_time": 1, "long_number": 1, "other_text": 1}}
```

Possible categories: `date_time`, `long_number`, `age`, `probable_name`, `keyword` and `other_text`. One area can
count in several categories. **All** detected text areas are masked, whatever their category.

OCR may miss very faint or very large text (more than 8 % of the image height). Review the image before sharing it.

## Errors

Every error arrives with `isError: true` and a message in `content[0].text`. There are two kinds.

**Errors from the server's own validation** carry a code in `structuredContent.error`. The message explains how to
fix the request.

| Code | When | Retry? |
|---|---|---|
| `invalid_input` | Not exactly one of `image_base64` / `image_path`, invalid base64, `image_path` over HTTP, or `sides`/`views` length does not match the views | No: fix the request |
| `unsupported_media_type` | The content is not PNG, JPEG, WebP, TIFF or BMP | No |
| `image_too_large` | It exceeds the maximum size or 60 MP | No: crop or reduce |
| `unreadable_image` | The file is damaged | No |
| `not_a_mammogram` | No view looks like a mammogram. `details.panels` holds the confidence of each view | No: use another image |
| `server_busy` | Too many analyses in progress | Yes, in a few seconds |
| `internal_error` | Unexpected failure. The detail stays only in the server log | Yes |

**Argument-validation errors raised by the MCP SDK itself** arrive as `isError: true` **without**
`structuredContent.error`. This happens with wrong argument types or values outside the schema, for example
`panel_count` outside 1–8 or a `sides` value other than `R`, `L` or `unknown`. Clients should treat an error
without a code as unexpected and retryable, and show the message.

Over HTTP these responses can also arrive before the tool runs:

| Response | When |
|---|---|
| 401 | The key is missing or not valid |
| 413 | The request is too large |
| 429 | Too many requests; it carries the `Retry-After` header |
| 421 | The `Host` is not in `MAMMO_ALLOWED_HOSTS` |
| 403 | The `Origin` is not allowed |

## Usage examples

**Claude Desktop (stdio).** With the configuration in the [README](../README.md), just ask, for example:
"Analyze the mammogram C:\path\rmlo.png; it is a right MLO."

**Over HTTP with the official Python client** (`mcp` 2.x; the headers go on the HTTP client, and an analysis can take
longer than the default timeouts):

```python
import httpx2
from mcp import Client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

http = create_mcp_http_client(headers={"Authorization": f"Bearer {key}"}, timeout=httpx2.Timeout(240))
async with Client(streamable_http_client("https://xxx.trycloudflare.com/mcp", http_client=http),
                  read_timeout_seconds=240) as c:
    result = await c.call_tool("mammo_analyze_mammogram",
                               {"image_base64": b64, "sides": ["L"], "views": ["MLO"], "language": "en"})
```

Behind Cloudflare Access (ADR 0006), Access stops a script before it reaches the server: it also needs an Access
service token (`CF-Access-Client-Id` and `CF-Access-Client-Secret` headers) allowed by the application's policy.

**Over HTTP with TypeScript:** see `src/infrastructure/mcp/mcpTriageGateway.ts` in the
[app repository](https://github.com/avalderrama/ai-mammogram-triage-app).
