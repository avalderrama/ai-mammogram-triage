"""CLI: `mammo-triage stdio` (local MCP clients such as Claude Desktop) or `mammo-triage http` (PWA / tunnel)."""
from __future__ import annotations

import argparse
import logging
import secrets
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mammo-triage", description="Mammogram triage MCP server (research use).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("stdio", help="Serve MCP over stdio (local clients). Allows image_path arguments.")
    http = sub.add_parser("http", help="Serve MCP over streamable HTTP at /mcp, protected by MAMMO_API_KEY.")
    http.add_argument("--host", help="Override MAMMO_HOST.")
    http.add_argument("--port", type=int, help="Override MAMMO_PORT.")
    sub.add_parser("new-key", help="Print a random API key to put in MAMMO_API_KEY.")
    args = parser.parse_args(argv)

    if args.command == "new-key":
        print(secrets.token_urlsafe(32))
        return

    from .settings import Settings
    settings = Settings()
    # stdout belongs to the MCP protocol in stdio mode: logs go to stderr. Image content is never logged.
    logging.basicConfig(stream=sys.stderr, level=settings.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .composition import build_http_app, build_mcp
    if args.command == "stdio":
        build_mcp(settings, allow_local_paths=True).run(transport="stdio")
        return

    import uvicorn
    app = build_http_app(settings)
    uvicorn.run(app, host=args.host or settings.host, port=args.port or settings.port,
                log_level=settings.log_level, proxy_headers=False, server_header=False, access_log=False)


if __name__ == "__main__":
    main()
