"""Explicit deployment configuration; no credentials in argv or access logs."""

import argparse
from pathlib import Path
import json
from urllib.parse import urlsplit

import uvicorn

from .app import create_app
from .policy import Policy


def main(argv=None):
    parser = argparse.ArgumentParser(description="DeedKit selected-tool MCP gateway")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=13240)
    parser.add_argument("--check", action="store_true", help="Validate without starting or printing secrets")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    origin = urlsplit(args.public_url)
    if origin.scheme != "https" and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("public connections require HTTPS")
    app = create_app(args.policy, args.public_url)
    if args.check:
        policy = Policy(args.policy).load()
        print(
            json.dumps(
                {"valid": True, "revision": policy["revision"], "modules": policy["deployment"]["modules"]}
            )
        )
        return
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        timeout_graceful_shutdown=10,
        limit_concurrency=128,
    )


if __name__ == "__main__":
    main()
