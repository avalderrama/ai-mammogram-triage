# Development guide

This document explains how to work on the server code: conventions, tests, how to make the most common changes
and what not to do.

## Conventions

- Code and documentation in English. User-facing text (the tool summaries) is in English by default, with Spanish
  as an option (`language: "es"`).
- Onion architecture with one package per ring (see [architecture.md](architecture.md)). The binding rules are in
  `reglas-clean-architecture-onion.md` (`CA-*`), outside this repository.
- `feature/...` branches. Never work directly on `main`.
- Expected errors are returned with `Result`, without exceptions; exceptions are for the unexpected.

## Tests

```bash
pytest
```

| Folder | What it covers |
|---|---|
| `tests/domain` | Values, triage policy and grouping by breast |
| `tests/application` | Use cases with fake ports |
| `tests/presentation` | MCP tools with a real in-process client, and HTTP security (401, 413, 429, CORS, `Host`, `/health`) |
| `tests/infrastructure` | Decoding (EXIF, pixel floods, formats), suspicious regions, model logic with fake networks (patch batches do not change scores), masking rules with a fake OCR engine, and bundle verification for both formats |
| `tests/host` | Insecure configurations that prevent startup |
| `tests/architecture` | `lint-imports` contracts; only `mammo_host` imports the infrastructure; only each backend module imports its libraries; starting the host loads no inference library |
| `tests/integration` | Real models through HTTP and MCP, and parity between the ONNX and PyTorch backends. Run separately |

For the integration tests:

```bash
pytest -m integration
```

They need a local library of test images that is not published (`MAMMO_TEST_IMAGES_DIR`) and the bundles:

- `test_real_models.py`: `MAMMO_MODELS_DIR`, in either format (run it once per format).
- `test_backend_parity.py`: `MAMMO_TORCH_MODELS_DIR` and `MAMMO_ONNX_MODELS_DIR`, the same bundle in both formats. It needs both extras installed (`".[onnx,torch,dev]"`). PyTorch runs on CPU in float32, so the gate, the probabilities, the patch scores, the heatmap presence and the regions must agree within 0.001. The patch cases (views of at least 1500 px) fail if the patch scanner stops running on them. Both backends must also keep reference outputs recorded from the PyTorch code before the backends were split, which catches a change in the shared numpy code. Finally, the ONNX masker must hide fake personal data, including 16 px text on a 4000 px image.

## Continuous integration

`.github/workflows/ci.yml` runs on every pull request and on `main`: `pytest` and `lint-imports` on Python 3.11 and
3.12, and `pip-audit` on the installed dependencies. A change that only touches documentation (Markdown files, `docs/`, `LICENSE`, `NOTICE`) skips the tests;
the **CI result** job always reports and is the only check the branch protection of `main` requires. The weights are private, so the integration tests do not run
there; run them locally (`pytest -m integration`) before changing the model code. Dependabot
(`.github/dependabot.yml`) opens weekly update pull requests for the Python packages and the GitHub Actions.

## Common changes

### Change the model or the thresholds

1. In the training repository, train and calibrate (`calibrate_fusion.py`).
2. Export the bundle with `export_bundle.py --out bundles/v2`, then the ONNX version with
   `export_onnx.py --bundle bundles/v2 --out bundles/v2-onnx` (it checks each network against PyTorch on real images).
3. Point `MAMMO_MODELS_DIR` to the new bundle and run the parity tests. No code change is needed if the model
   outputs and the format of `fusion_calibration.json` do not change.
4. If the preprocessing changes, `ml/preprocessing.py` must stay **identical** to `common.preprocess` in the
   training repository.

### Add an MCP tool

1. If it is a new use case, create the command or query, its DTO and its *handler* in `mammo_application`.
2. If needed, define a new port in `abstractions/ports.py` and implement it in `_adapters.py`.
3. Register the tool in `mammo_mcp/server.py`, wrapped in `guarded(...)` so its errors carry a code. Accept
   `language: SummaryLanguage = "en"` if it returns a text summary.
4. Wire the pieces in `mammo_host/composition.py`.
5. Add tests in `tests/presentation/test_mcp_tools.py`.

### Change summary texts

`mammo_mcp/formatting.py`. The `_TEXT` dictionary holds the English (`en`) and Spanish (`es`) texts; keep both in
sync. `structuredContent` and `DISCLAIMER` in the payload stay in English. The app's texts live in its own
repository ([ai-mammogram-triage-app](https://github.com/avalderrama/ai-mammogram-triage-app)), in
`src/presentation/i18n/en.ts` and `src/presentation/i18n/es.ts`.

## What not to do

- Import `mammo_infrastructure` from the presentation. The contracts would fail.
- Log images, tool arguments or OCR text.
- Commit weights, data or images. `.gitignore` excludes them; do not force it.
- Load weights with `weights_only=False`.
