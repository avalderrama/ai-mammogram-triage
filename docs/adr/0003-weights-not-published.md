# ADR 0003 — Weights are not published; they are loaded from a verified bundle

- **Status:** accepted (2026-10-20)

## Context

The models were trained on VinDr-Mammo. Its PhysioNet data use agreement requires each person to sign the
agreement and forbids redistributing the data. The derived weights are treated with the same caution.

## Decision

- The public repositories contain no weights, data or medical images; `.gitignore` excludes them.
- The private training repository exports a **versioned bundle** (`export_bundle.py`) with the four weights, the
  calibration and a SHA-256 manifest.
- The server reads it from `MAMMO_MODELS_DIR`. At startup it checks that all files are present and their *hashes*
  match, and it loads the weights with `torch.load(weights_only=True)`.

## Consequences

- Anyone who clones the public repository cannot run the model without the bundle. This is intended.
- The backup of the bundle (~65 MB) must go to private storage: an encrypted *bucket* or a private release.
