"""Stdio entry point for hosts that load keys from private files rather than config/env."""

import argparse
import asyncio
import json
import logging
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent

from family_calendar.errors import CalendarError
from family_calendar.private_files import read_private


def proxy_config(url, key_file):
    value = urlsplit(url)
    if (
        value.scheme != "http"
        or value.hostname not in ("127.0.0.1", "localhost")
        or value.username
        or value.password
        or not value.port
        or value.path != "/mcp"
        or value.query
        or value.fragment
    ):
        raise CalendarError("unsafe_mcp_endpoint")
    key = read_private(key_file).decode().strip()
    if not key or len(key) > 512 or any(c.isspace() for c in key):
        raise CalendarError("invalid_agent_key")
    return url, key


async def run(url, key):
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + key}, trust_env=False, follow_redirects=False, timeout=30
    ) as client:
        async with streamable_http_client(url, http_client=client) as (read, write):
            async with ClientSession(read, write) as remote:
                await remote.initialize()

                async def tools(context, params):
                    return await remote.list_tools(params=params)

                async def call(context, params):
                    name, arguments = params.name, params.arguments
                    try:
                        return await remote.call_tool(name, arguments or {})
                    except Exception:
                        result = {
                            "error": "service_unavailable",
                            "next_action": "query operation using its existing request_id; never replace an unknown create",
                        }
                        return CallToolResult(
                            content=[TextContent(type="text", text=json.dumps(result))],
                            structuredContent=result,
                            isError=True,
                        )

                server = Server("family-calendar", version="0.2.0", on_list_tools=tools, on_call_tool=call)

                async with stdio_server() as (input_stream, output_stream):
                    await server.run(input_stream, output_stream, server.create_initialization_options())


def main():
    logging.disable(logging.CRITICAL)
    p = argparse.ArgumentParser(description="Family Calendar authenticated local MCP proxy")
    p.add_argument("--url", default="http://127.0.0.1:13232/mcp")
    p.add_argument("--key-file", required=True, type=Path)
    args = p.parse_args()
    try:
        url, key = proxy_config(args.url, args.key_file)
        asyncio.run(run(url, key))
    except KeyboardInterrupt:
        pass
    except Exception:
        # Do not let transport tracebacks reveal auth or source data on MCP stderr.
        print("family-calendar: service unavailable or private key invalid", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
