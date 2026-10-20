# ADR 0004 — Pilot on the owner's PC, with an HTTPS tunnel and a shared key

- **Status:** accepted (2026-10-20)

## Context

The pilot is short and only a few doctors use it. There is no budget or need for the cloud, and the GPU is in the
owner's PC.

## Decision

- The server listens only on `127.0.0.1`.
- External access goes through Cloudflare quick tunnels (`cloudflared`), without opening router ports. There is one
  for the app and another for the server.
- A **shared key** protects `/mcp`.
- `scripts/start_pilot.ps1` automates the startup (parameters in [operations.md](../operations.md)).

## Consequences

- The PC must be on. The URLs change on every start.
- There is no per-doctor traceability. For anything beyond a short trial: Cloudflare Access or per-doctor keys, and
  a named tunnel for a fixed URL.
- With the tunnel, all clients arrive from `127.0.0.1`. That is why the rate limit uses `CF-Connecting-IP`,
  accepted only from the machine itself.
