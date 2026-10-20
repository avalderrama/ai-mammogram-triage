# ADR 0002 — A single interface: MCP (no separate REST API)

- **Status:** accepted (2026-10-20)

## Context

The goal is that "anyone can access" the model: assistants such as Claude, and the test app for doctors.
Maintaining both a REST API and an MCP server would duplicate validation, security and tests.

## Decision

The only interface is the MCP server, with two transports:

- **stdio** for local clients;
- **streamable HTTP**, stateless and with JSON responses, for the app and remote clients.

The app uses the official MCP SDK for TypeScript.

## Consequences

- There is a single contract: the tools and their `structuredContent`. HTTP security is written once
  (`mammo_api`).
- Each analysis costs about 3 HTTP requests (initialize, initialized and tools/call). Keep this in mind when
  setting the rate limit.
- Images travel as base64 inside JSON-RPC, 33 % larger. This is acceptable with the 10 MB limit.
- The server does not accept image URLs, to avoid SSRF.
