"""Reproducible loopback HTTP load. Synthetic domains, real gateway/MCP transports.

No provider sessions, private runtime configuration or external calls are loaded.
Raw per-request timings and process measurements are retained with the git SHA.
"""

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

import httpx
import psutil
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_helpers import gateway, port, server, ROOT

BLOCK = b"x" * 65536
FILE_BYTES = 64 * 1024 * 1024
FILE_SHA = hashlib.sha256(BLOCK * (FILE_BYTES // len(BLOCK))).hexdigest()


def backend(base):
    async def operation(request):
        name = request.path_params["operation"]
        if name == "capabilities":
            return JSONResponse({"permissions": ["read", "download", "calls:read"]})
        if name == "get_call_status":
            await asyncio.sleep(40)
        if name == "get_attachment":
            return JSONResponse(
                {
                    "download_url": base + "/v1/download/" + "a" * 43,
                    "download_expires_in": 120,
                    "bytes": FILE_BYTES,
                    "sha256": FILE_SHA,
                    "filename": "synthetic.bin",
                }
            )
        return JSONResponse(
            {
                "operation": name,
                "coverage": "synthetic_complete",
                "items": [
                    {"id": str(i), "text": "synthetic fixture " + str(i)}
                    for i in range(8)
                ],
            }
        )

    async def download(request):
        async def body():
            for _ in range(FILE_BYTES // len(BLOCK)):
                await asyncio.sleep(0.015)
                yield BLOCK

        return StreamingResponse(body(), headers={"Content-Length": str(FILE_BYTES)})

    return Starlette(
        routes=[
            Route("/v1/{operation}", operation, methods=["POST"]),
            Route("/v1/download/{grant}", download, methods=["GET", "HEAD"]),
        ]
    )


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


@asynccontextmanager
async def contention(client):
    async def busy_call():
        try:
            await client.post(
                "/v1/calls_get_call_status", json={"call_id": "synthetic"}
            )
        except httpx.HTTPError:
            pass

    async def busy_file():
        result = (
            await client.post(
                "/v1/messages_get_attachment", json={"attachment_id": "synthetic"}
            )
        ).json()
        async with client.stream("GET", result["download_url"]) as response:
            async for _ in response.aiter_bytes(65536):
                await asyncio.sleep(0.01)

    tasks = [asyncio.create_task(busy_call()) for _ in range(16)]
    tasks += [asyncio.create_task(busy_file()) for _ in range(2)]
    try:
        await asyncio.sleep(0.1)
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def measure(client, path, payload, config, child, mcp=False):
    rows, lag, resources = [], [], []

    async def sample(index):
        started = time.perf_counter()
        status, error, size = 0, "", 0
        try:
            response = await client.post(path, json=payload)
            status, size = response.status_code, len(response.content)
            value = response.json()
            if mcp:
                assert not value["result"].get("isError")
                value = value["result"]["structuredContent"]
            assert (
                status == 200
                and value["coverage"] == "synthetic_complete"
                and len(value["items"]) == 8
            )
        except Exception as exc:
            error = type(exc).__name__
        return {
            "n": index,
            "ms": (time.perf_counter() - started) * 1000,
            "status": status,
            "error": error,
            "bytes": size,
        }

    async def monitor():
        while True:
            before = time.perf_counter()
            await asyncio.sleep(0.02)
            lag.append(max(0, time.perf_counter() - before - 0.02) * 1000)
            try:
                resources.append(
                    {"rss": child.memory_info().rss, "fds": child.num_fds()}
                )
            except psutil.Error:
                pass

    for i in range(config["warmup"]):
        row = await sample(i)
        assert not row["error"], "warmup failed"
    started = time.perf_counter()
    cpu_before = sum(child.cpu_times()[:2])
    observer = asyncio.create_task(monitor())
    indices = iter(range(config["samples"]))

    async def worker():
        for index in indices:
            rows.append(await sample(index))

    try:
        await asyncio.gather(*(worker() for _ in range(config["concurrency"])))
    finally:
        observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)
    duration = time.perf_counter() - started
    timings = [row["ms"] for row in rows]
    return {
        "samples": len(rows),
        "errors": sum(bool(r["error"]) for r in rows),
        "p50_ms": percentile(timings, 0.5),
        "p95_ms": percentile(timings, 0.95),
        "payload_bytes": sorted({r["bytes"] for r in rows}),
        "duration_s": duration,
        "gateway_cpu_s": sum(child.cpu_times()[:2]) - cpu_before,
        "gateway_peak_rss": max(
            (r["rss"] for r in resources), default=child.memory_info().rss
        ),
        "gateway_peak_fds": max((r["fds"] for r in resources), default=child.num_fds()),
        "load_generator_loop_lag_p95_ms": percentile(lag, 0.95) if lag else 0,
        "raw": sorted(rows, key=lambda r: r["n"]),
    }


async def run(base, gateway_base, config, child, output):
    results = []
    limits = httpx.Limits(max_connections=64, max_keepalive_connections=64)
    async with (
        httpx.AsyncClient(
            base_url=base, timeout=10, trust_env=False, limits=limits
        ) as direct,
        httpx.AsyncClient(
            base_url=gateway_base,
            timeout=45,
            trust_env=False,
            limits=limits,
            headers={
                "Authorization": "Bearer synthetic-gateway",
                "Accept": "application/json, text/event-stream",
            },
        ) as common,
    ):
        for repeat in range(config["repeats"]):
            for mode in ("isolated", "contended"):

                async def batch():
                    for operation, tool, args in [
                        ("search", "messages_search", {"query": "synthetic"}),
                        ("calendars", "calendar_calendars", {}),
                    ]:
                        for transport in ("direct", "rest", "mcp"):
                            payload = (
                                args
                                if transport != "mcp"
                                else {
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "tools/call",
                                    "params": {"name": tool, "arguments": args},
                                }
                            )
                            path = (
                                "/v1/" + operation
                                if transport == "direct"
                                else "/v1/" + tool
                                if transport == "rest"
                                else "/mcp"
                            )
                            result = await measure(
                                direct if transport == "direct" else common,
                                path,
                                payload,
                                config,
                                child,
                                transport == "mcp",
                            )
                            result.update(
                                repeat=repeat,
                                mode=mode,
                                operation=operation,
                                transport=transport,
                            )
                            results.append(result)
                            output.write_text(
                                json.dumps(
                                    {
                                        "status": "running",
                                        "config": config,
                                        "results": results,
                                    }
                                )
                            )

                if mode == "contended":
                    async with contention(common):
                        await batch()
                else:
                    await batch()
    return results


def evaluate(results, budget):
    failures = []
    for row in results:
        identity = (
            f"{row['repeat']}/{row['mode']}/{row['operation']}/{row['transport']}"
        )
        if row["samples"] < 1000 or row["errors"] > budget["max_errors"]:
            failures.append(identity + ": incomplete or failed requests")
        if (
            row["gateway_peak_rss"] > budget["gateway_rss_mb"] * 1024**2
            or row["gateway_peak_fds"] > budget["gateway_fd_limit"]
        ):
            failures.append(identity + ": process resource budget")
        paired = [
            r
            for r in results
            if r["repeat"] == row["repeat"] and r["operation"] == row["operation"]
        ]
        direct = next(
            r for r in paired if r["mode"] == row["mode"] and r["transport"] == "direct"
        )
        if row["transport"] != "direct" and (
            row["p95_ms"] > budget["gateway_p95_ms"]
            or row["p95_ms"]
            > direct["p95_ms"] * budget["gateway_vs_direct_ratio"]
            + budget["gateway_vs_direct_slack_ms"]
        ):
            failures.append(identity + ": gateway overhead budget")
        isolated = next(
            r
            for r in paired
            if r["mode"] == "isolated" and r["transport"] == row["transport"]
        )
        if (
            row["mode"] == "contended"
            and row["p95_ms"]
            > isolated["p95_ms"] * budget["contention_ratio"]
            + budget["contention_slack_ms"]
        ):
            failures.append(identity + ": load isolation budget")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--junit", type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    assert config["samples"] >= 1000 and config["repeats"] >= 3
    random.seed(config["seed"])
    selected_port = port()
    base = f"http://127.0.0.1:{selected_port}"
    with (
        tempfile.TemporaryDirectory(prefix="deedkit-benchmark-") as temporary,
        server(backend(base), selected_port),
    ):
        path = Path(temporary)
        backends = {
            name: (base, "synthetic") for name in ("calendar", "messaging", "calls")
        }
        scopes = {
            "calendar": [],
            "messaging": ["read", "download"],
            "calls": ["calls:read"],
        }
        tools = [
            "calendar_calendars",
            "messages_search",
            "messages_get_attachment",
            "calls_get_call_status",
        ]
        with gateway(path, backends, scopes, tools) as (client, _, _):
            child = next(
                p
                for p in psutil.Process().children()
                if "agent_gateway.cli" in p.cmdline()
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            results = asyncio.run(
                run(base, str(client.base_url), config, child, args.output)
            )
    failures = evaluate(results, config)
    report = {
        "version": 1,
        "status": "failed" if failures else "passed",
        "failures": failures,
        "sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
        ),
        "config": config,
        "results": results,
        "scope": "Synthetic backend HTTP responses; real gateway REST/MCP. Not provider latency. Loop lag measured in load generator; gateway scheduling observed through end-to-end latency.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report))
    print(
        json.dumps(
            {
                "status": report["status"],
                "requests": sum(r["samples"] for r in results),
                "failures": failures,
                "maximum_gateway_p95_ms": max(
                    r["p95_ms"] for r in results if r["transport"] != "direct"
                ),
            }
        )
    )
    if args.junit:
        suite = ET.Element("testsuite", name="gateway_load", tests=str(len(results)))
        for row in results:
            identity = (
                f"{row['repeat']}/{row['mode']}/{row['operation']}/{row['transport']}"
            )
            case = ET.SubElement(
                suite,
                "testcase",
                name=identity,
                classname="tests.platform.benchmarks.gateway_read_load",
                file="tests/platform/benchmarks/gateway_read_load.py",
            )
            errors = [
                failure for failure in failures if failure.startswith(identity + ":")
            ]
            if errors:
                ET.SubElement(case, "failure", message="; ".join(errors))
        args.junit.parent.mkdir(parents=True, exist_ok=True)
        args.junit.write_bytes(
            ET.tostring(suite, encoding="utf-8", xml_declaration=True)
        )
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
