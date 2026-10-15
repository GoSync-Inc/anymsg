"""Owned synthetic HTTP processes; never uses a provider or production configuration."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import threading
import time

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def server(app, selected_port):
    service = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=selected_port,
            access_log=False,
            log_level="critical",
            timeout_graceful_shutdown=3,
        )
    )
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not service.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.started, "synthetic backend failed to start"
        yield
    finally:
        service.should_exit = True
        thread.join(5)
        assert not thread.is_alive(), "owned synthetic backend did not stop"


@contextmanager
def gateway(tmp_path, backends, permissions, tools, *, actions=None):
    selected_port = port()
    base = f"http://127.0.0.1:{selected_port}"
    credentials = {}
    for module, (_, key) in backends.items():
        path = tmp_path / f"{module}.key"
        path.write_text(key)
        path.chmod(0o600)
        credentials[module] = {
            "key_file": str(path),
            "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
        }
    document = {
        "version": 1,
        "revision": 1,
        "deployment": {
            "modules": list(backends),
            "backends": {name: {"url": url} for name, (url, _) in backends.items()},
        },
        "profiles": {"selected": {"tools": tools, "actions": actions or {}}},
        "principals": [
            {
                "id": "synthetic-agent",
                "key_sha256": hashlib.sha256(b"synthetic-gateway").hexdigest(),
                "profile": "selected",
                "enabled": True,
                "expires_at": "2099-01-01T00:00:00Z",
                "grants": permissions,
                "credentials": credentials,
                "actions": actions or {},
            }
        ],
    }
    policy = tmp_path / "policy.json"

    def save():
        policy.write_text(json.dumps(document))
        policy.chmod(0o600)

    save()
    with (tmp_path / "gateway.log").open("wb") as log:
        process = subprocess.Popen(
            [
                str(ROOT / "services/gateway/.venv/bin/python"),
                "-m",
                "agent_gateway.cli",
                "--policy",
                str(policy),
                "--public-url",
                base,
                "--port",
                str(selected_port),
            ],
            cwd=tmp_path,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(
                base_url=base,
                timeout=10,
                trust_env=False,
                headers={"Authorization": "Bearer synthetic-gateway"},
            ) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert process.poll() is None, "owned gateway exited before startup"
                    try:
                        if client.get("/healthz").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.03)
                else:
                    raise AssertionError("owned gateway failed to start")
                yield client, document, save
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise AssertionError("gateway graceful shutdown failed")


def call(client, name, arguments):
    response = client.post("/v1/" + name, json=arguments)
    assert response.status_code == 200, response.text
    return response.json()
