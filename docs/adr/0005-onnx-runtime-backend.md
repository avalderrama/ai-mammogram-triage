# ADR 0005 — ONNX Runtime + RapidOCR as the lightweight inference backend

- **Status:** accepted (2026-10-20)

## Context

The pilot has no users yet and the goal is to host the server for free: the free Azure VMs (B1s, B2ats v2,
B2pts v2) have 1 GB of RAM. With PyTorch, timm and EasyOCR, one analysis peaks at ~5 GB on CPU and the process
stays at ~1–5 GB afterwards. The networks are four EfficientNet-B0 models; PyTorch is only needed to run them.

## Decision

- Add a second inference backend in the infrastructure ring:
  - the networks are exported to ONNX (`export_onnx.py` in the training repository) and run with ONNX Runtime on CPU;
  - text is masked with RapidOCR (PP-OCR models on ONNX Runtime) instead of EasyOCR.
- The bundle's `manifest.json` declares its format (`onnx` or `torch`). `create_ports()` loads only the matching
  backend, so an ONNX server does not need PyTorch installed.
- The model logic (preprocessing, patch scan, gate, regions, heatmap) and the masking rules are shared numpy code.
  Each backend only provides the networks and the OCR engine. The domain, application and presentation rings do not
  change.
- Both backends stay: PyTorch for GPU servers and research, ONNX for small CPU servers.
- RapidOCR settings that keep the masking rules equivalent:
  - minimum confidence 0.3, the masking rule (RapidOCR's default is 0.5);
  - its boxes are measured without their ~15 % padding before the "too tall to be text" rule;
  - images are handed over as BGR, which is what RapidOCR expects from an array;
  - `det_limit_type="max"`: by default the detector enlarges images until the short side is 736 px, so a wide
    2720×453 figure was processed at ~4400 px and the peak memory doubled;
  - images larger than 1024 px are read in overlapping 1024 px tiles at full resolution (`TextMasker`). Shrinking
    them instead bounded the memory too, but lost text under ~0.5 % of the image height. Text read twice in the
    overlap is masked and counted once.

## Evidence (2026-10-20)

- **Same results.** On half B of the VinDr test split (1,000 breasts, the half never used for calibration), the AUCs
  differ by at most 0.0005 and 6 of 1,000 breasts change level. The cause is the reference, computed on GPU in
  float16: PyTorch in float32 on CPU gives the same scores as ONNX (maximum difference 0.0000016).
  `tests/integration/test_backend_parity.py` keeps this checked, including the patch scanner and the regions on views of
  at least 1500 px and reference outputs recorded from the PyTorch code before the split.
- **Masking.** On fake personal data drawn over 24 mammograms: 60/60 lines hidden with RapidOCR, 58/60 with EasyOCR.
  On a 12 MP image, RapidOCR with tiles hid every line down to text 0.3 % of the image high (12 px). Reading the whole
  image at once missed half of them at that size (and peaked at 0.92 GB), and shrinking it to 1600 px missed every
  line under 0.5 %. On the real test library, RapidOCR covers 97 % of EasyOCR's
  masked area on average (lowest 84 %), and it also masks small print EasyOCR misses. What it misses are short view
  labels or panel letters, not personal data.
- **Resources.** For the full HTTP + MCP app on 5 images (viewer capture, multi-view figure, two 1630×2048 views
  and a 12 MP photo), on a 4-core CPU:

| | PyTorch + EasyOCR | ONNX Runtime + RapidOCR |
|---|---|---|
| Startup | 6.4 s | 0.9 s |
| Peak memory | 5.1 GB | 0.60 GB |
| Memory at rest afterwards | 4.9 GB | 0.28 GB |
| Time per image | 1.0–9.7 s | 1.1–2.9 s; 6.7 s for the 12 MP photo (20 OCR tiles) |

## Consequences

- The server fits in a 1 GB VM, with little margin. Use a swap file and measure on the real machine before inviting
  users.
- Every new bundle must be exported in both formats and pass the parity tests (`docs/development.md`).
- RapidOCR's recognition models are trained for Chinese and English. Finding where text is, which is what masking
  needs, works for Spanish; the counted categories (date, name…) may be less accurate. The read text is never stored.
- RapidOCR depends on `opencv-python`, which needs `libGL` on Linux. Headless servers need `opencv-python-headless`
  or the system library (`docs/operations.md`).
