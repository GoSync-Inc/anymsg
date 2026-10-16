"""Opt-in HTTP release admission and private, local operator control.

This module deliberately imports no web framework or provider code. A request is
active until its complete ASGI call returns, including a blocked body send.
"""
import asyncio
from contextlib import suppress
import inspect
import json
import os
from pathlib import Path
import re
import stat


class ReleaseRuntime:
    def __init__(self, generation, sha, socket_path, readiness, *, pending_tickets=lambda: 0,
                 health_paths=("/healthz",), file_paths=()):
        self.generation, self.sha, self.socket_path = generation, sha, socket_path
        self.readiness, self.pending_tickets = readiness, pending_tickets
        self.health_paths, self.file_paths = health_paths, file_paths
        self.phase, self.active_requests = "warming", 0
        self._startup_complete = False
        self._lock = asyncio.Lock()
        self._server, self._inode = None, None
        self._connections = set()

    @classmethod
    def from_env(cls, readiness, **kwargs):
        names = ("DEEDKIT_RELEASE_ID", "DEEDKIT_RELEASE_SHA", "DEEDKIT_RELEASE_SOCKET")
        if not any(name in os.environ for name in names):
            return None
        generation, sha, path = (os.environ.get(name, "") for name in names)
        if (not re.fullmatch(r"[0-9a-f]{12}", generation)
                or not re.fullmatch(r"[0-9a-f]{40}", sha)
                or not path.startswith("/") or "\x00" in path
                or len(os.fsencode(path)) > 103 or Path(path).name != "control.sock"):
            raise ValueError("invalid_release_configuration")
        return cls(generation, sha, path, readiness, **kwargs)

    async def _ready(self):
        if not self._startup_complete:
            return False
        try:
            async with asyncio.timeout(3):
                result = self.readiness()
                if inspect.isawaitable(result):
                    result = await result
                return result is True and self._startup_complete
        except Exception:
            return False

    async def control(self, action):
        async with self._lock:
            if action not in {"status", "activate", "drain"}:
                return {"error": "invalid_control_request"}
            if action == "drain":
                self.phase = "draining"
            ready = self.phase != "draining" and await self._ready()
            try:
                pending = self.pending_tickets()
                if type(pending) is not int or pending < 0:
                    raise ValueError
            except Exception:
                # Unknown is never interpreted as an empty admission window.
                pending, ready = -1, False
            if action == "activate" and self.phase == "warming" and ready:
                self.phase = "active"
            return {"protocol": 1, "generation": self.generation, "sha": self.sha,
                    "phase": self.phase, "ready": ready,
                    "active_requests": self.active_requests, "pending_tickets": pending}

    async def start(self):
        if self._server is not None:
            raise ValueError("release_control_already_started")
        parent = Path(self.socket_path).parent
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != os.geteuid() or parent.resolve() != parent):
            raise ValueError("private_release_directory_required")
        if os.path.lexists(self.socket_path):
            # Never unlink another process's endpoint, including a stale socket.
            raise ValueError("release_control_path_exists")
        self._server = await asyncio.start_unix_server(self._connection, self.socket_path,
                                                       limit=1024, start_serving=False)
        os.chmod(self.socket_path, 0o600)
        self._inode = os.lstat(self.socket_path).st_ino
        await self._server.start_serving()

    async def _connection(self, reader, writer):
        task = asyncio.current_task()
        if len(self._connections) >= 16:
            writer.close()
            return
        self._connections.add(task)
        try:
            async with asyncio.timeout(5):
                line = await asyncio.wait_for(reader.readline(), 2)
                value = json.loads(line)
                if (not line.endswith(b"\n") or len(line) > 1024 or not isinstance(value, dict)
                        or set(value) != {"action"} or not isinstance(value["action"], str)):
                    raise ValueError
                result = await self.control(value["action"])
                writer.write(json.dumps(result, separators=(",", ":")).encode() + b"\n")
                await asyncio.wait_for(writer.drain(), 2)
        except (ValueError, UnicodeDecodeError, TimeoutError, ConnectionError):
            # Invalid/oversize lines fail closed without echoing request bytes.
            pass
        finally:
            self._connections.discard(task)
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    async def close(self):
        self._startup_complete = False
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        tasks = list(self._connections)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._inode is not None:
            with suppress(FileNotFoundError):
                if os.lstat(self.socket_path).st_ino == self._inode:
                    os.unlink(self.socket_path)
            self._inode = None

    def wrap(self, app):
        runtime = self

        class Released:
            def __getattr__(self, name):
                return getattr(app, name)

            async def __call__(self, scope, receive, send):
                if scope["type"] == "lifespan":
                    runtime._startup_complete = False
                    async def lifecycle_receive():
                        message = await receive()
                        if message["type"] == "lifespan.shutdown":
                            runtime._startup_complete = False
                            runtime.phase = "draining"
                        return message

                    async def lifecycle_send(message):
                        kind = message["type"]
                        if kind in {"lifespan.startup.failed", "lifespan.shutdown.failed",
                                    "lifespan.shutdown.complete"}:
                            runtime._startup_complete = False
                        await send(message)
                        if kind == "lifespan.startup.complete":
                            runtime._startup_complete = True

                    try:
                        await runtime.start()
                    except Exception:
                        await send({"type": "lifespan.startup.failed",
                                    "message": "release_control_unavailable"})
                        return
                    try:
                        return await app(scope, lifecycle_receive, lifecycle_send)
                    finally:
                        await runtime.close()
                if scope["type"] != "http":
                    return await app(scope, receive, send)
                runtime.active_requests += 1
                try:
                    method, path = scope.get("method"), scope.get("path", "")
                    health = method in {"GET", "HEAD"} and path in runtime.health_paths
                    file_request = method in {"GET", "HEAD"} and any(
                        path.startswith(prefix) for prefix in runtime.file_paths)
                    if (runtime.phase == "warming" and not health) or (
                            runtime.phase == "draining" and not (health or file_request)):
                        body = b'{"error":"release_unavailable"}'
                        await send({"type": "http.response.start", "status": 503, "headers": [
                            (b"content-type", b"application/json"), (b"cache-control", b"no-store"),
                            (b"retry-after", b"1"), (b"content-length", str(len(body)).encode())]})
                        return await send({"type": "http.response.body", "body": body})
                    return await app(scope, receive, send)
                finally:
                    runtime.active_requests -= 1

        return Released()
