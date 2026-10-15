import asyncio
import json
import socket

import httpx
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import uvicorn

from agent_gateway.app import create_app
from test_policy import config


def test_sdk_client_real_loopback_lifecycle_reconnect_and_revocation(tmp_path):
    async def run():
        path = tmp_path / "policy.json"
        key = tmp_path / "backend.key"
        key.write_text("backend-calendar-a")
        key.chmod(0o600)
        value = config()
        value["principals"][0]["credentials"]["calendar"]["key_file"] = str(key)
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        calls = []

        async def handler(request):
            if request.url.path == "/v1/capabilities":
                return httpx.Response(200, json={"permissions": ["agenda:read", "availability:read"]})
            calls.append(request.url.path)
            return httpx.Response(200, json={"coverage": "unknown", "calendars": []})

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            url = "http://127.0.0.1:" + str(sock.getsockname()[1])
            app = create_app(path, url, transports={"calendar": httpx.MockTransport(handler)})
            server = uvicorn.Server(
                uvicorn.Config(app, access_log=False, log_level="critical", timeout_graceful_shutdown=1)
            )
            task = asyncio.create_task(server.serve(sockets=[sock]))
            try:
                async with asyncio.timeout(10):
                    while not server.started:
                        if task.done():
                            await task
                        await asyncio.sleep(0.01)
                    for attempt in range(2):
                        async with httpx2.AsyncClient(headers={"Authorization": "Bearer gateway-a"}) as http:
                            async with streamable_http_client(url + "/mcp", http_client=http) as streams:
                                async with ClientSession(*streams) as client:
                                    initialized = await client.initialize()
                                    assert initialized.server_info.name == "DeedKit"
                                    listed = await client.list_tools()
                                    assert {t.name for t in listed.tools} == set(
                                        value["profiles"]["reader"]["tools"]
                                    )
                                    result = await client.call_tool("calendar_calendars", {})
                                    assert not result.is_error
                                    assert result.structured_content["coverage"] == "unknown"
                                    if attempt:
                                        value["revision"] += 1
                                        value["profiles"]["reader"]["tools"] = ["platform_capabilities"]
                                        path.write_text(json.dumps(value))
                                        denied = await client.call_tool("calendar_calendars", {})
                                        assert denied.is_error
                                        assert [t.name for t in (await client.list_tools()).tools] == [
                                            "platform_capabilities"
                                        ]
            finally:
                server.should_exit = True
                await asyncio.wait_for(task, 4)
            assert app.state.clients.closed
            assert app.state.tickets.entries == {}
            assert calls == ["/v1/calendars", "/v1/calendars"]

    asyncio.run(run())
