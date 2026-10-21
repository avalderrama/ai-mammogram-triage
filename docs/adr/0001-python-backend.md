# ADR 0001 — Python backend, not .NET

- **Status:** accepted (2026-10-20)

## Context

The models were trained in PyTorch with `timm`, with custom preprocessing that crops the breast and puts the chest
wall on the left. The team's architecture rules are written with .NET in mind.

## Decision

The server is written in Python and applies the `CA-*` rules, adapted:

- one package per ring in `src/`;
- ports as `Protocol`;
- `Result` for expected errors;
- contracts checked with `import-linter`.

## Consequences

- Inference uses exactly the same code and versions as training: no risk of differences when exporting to ONNX,
  and no need to rewrite the preprocessing.
- If the organization requires .NET, a .NET layer can call this service, or the models can be exported to ONNX.
  That would be extra work.
