"""Synthetic release admission, socket isolation and full-response drain proofs."""
import asyncio
import json
from pathlib import Path
import stat
import tempfile

import pytest


@pytest.fixture
def tmp_path():
    # macOS Unix sockets have a 104-byte path limit; pytest's default is longer.
    with tempfile.TemporaryDirectory(prefix="dkr-", dir=str(Path("/tmp").resolve())) as path:
        yield Path(path)


def runtime(tmp_path, monkeypatch, ready=lambda: True, pending=lambda: 0, *, started=True):
    from platform_contracts.release import ReleaseRuntime
    tmp_path.chmod(0o700)
    monkeypatch.setenv("DEEDKIT_RELEASE_ID", "012345abcdef")
    monkeypatch.setenv("DEEDKIT_RELEASE_SHA", "a" * 40)
    monkeypatch.setenv("DEEDKIT_RELEASE_SOCKET", str(tmp_path / "control.sock"))
    async def readiness():
        return ready()
    release = ReleaseRuntime.from_env(readiness, pending_tickets=pending,
        file_paths=("/v1/files/",))
    # Request-counter units model an already-started application. Lifecycle race
    # regressions below run the real wrapper and socket with this gate closed.
    release._startup_complete = started
    return release


async def request(app, path="/v1/query", method="POST", send=None):
    frames = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def collect(frame):
        frames.append(frame)
        if send:
            await send(frame)
    await app({"type": "http", "path": path, "method": method}, receive, collect)
    return frames


async def ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"fixture"})


def test_release_environment_is_opt_in_and_incomplete_identity_is_rejected(monkeypatch):
    from platform_contracts.release import ReleaseRuntime
    for name in ("ID", "SHA", "SOCKET"):
        monkeypatch.delenv("DEEDKIT_RELEASE_" + name, raising=False)
    assert ReleaseRuntime.from_env(lambda: True) is None
    monkeypatch.setenv("DEEDKIT_RELEASE_ID", "012345abcdef")
    with pytest.raises(ValueError, match="invalid_release_configuration"):
        ReleaseRuntime.from_env(lambda: True)


def test_release_unready_activation_and_irreversible_drain(tmp_path, monkeypatch):
    async def scenario():
        state = [False]
        release = runtime(tmp_path, monkeypatch, ready=lambda: state[0])
        app = release.wrap(ok)
        assert (await request(app))[0]["status"] == 503
        assert (await request(app, "/healthz", "GET"))[0]["status"] == 200
        assert (await release.control("activate"))["phase"] == "warming"
        state[0] = True
        assert (await release.control("activate"))["phase"] == "active"
        assert (await request(app))[0]["status"] == 200
        assert (await release.control("drain"))["phase"] == "draining"
        assert (await request(app))[0]["status"] == 503
        assert (await request(app, "/v1/files/fixture", "GET"))[0]["status"] == 200
        assert (await request(app, "/v1/files/fixture", "POST"))[0]["status"] == 503
        assert (await release.control("activate"))["phase"] == "draining"
    asyncio.run(scenario())


def test_release_counts_until_response_send_finishes_and_on_cancel(tmp_path, monkeypatch):
    async def scenario():
        release = runtime(tmp_path, monkeypatch)
        await release.control("activate")
        entered, finish = asyncio.Event(), asyncio.Event()
        async def blocked(frame):
            if frame["type"] == "http.response.body":
                entered.set()
                await finish.wait()
        task = asyncio.create_task(request(release.wrap(ok), send=blocked))
        await entered.wait()
        assert (await release.control("drain"))["active_requests"] == 1
        assert (await release.control("status"))["active_requests"] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await release.control("status"))["active_requests"] == 0
    asyncio.run(scenario())


def test_release_socket_is_private_bounded_and_reports_tickets(tmp_path, monkeypatch):
    async def scenario():
        release = runtime(tmp_path, monkeypatch, pending=lambda: 3)
        await release.start()
        path = Path(release.socket_path)
        try:
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            reader, writer = await asyncio.open_unix_connection(str(path))
            writer.write(b'{"action":"status"}\n')
            await writer.drain()
            status = json.loads(await reader.readline())
            writer.close()
            await writer.wait_closed()
            assert status == {"protocol": 1, "generation": "012345abcdef", "sha": "a" * 40,
                "phase": "warming", "ready": True, "active_requests": 0, "pending_tickets": 3}
            reader, writer = await asyncio.open_unix_connection(str(path))
            writer.write(b"x" * 2048 + b"\n")
            await writer.drain()
            assert len(await reader.read()) < 1024
            writer.close()
            await writer.wait_closed()
        finally:
            await release.close()
        assert not path.exists()
    asyncio.run(scenario())


def test_release_rejects_shared_directory_and_existing_socket_path(tmp_path, monkeypatch):
    async def scenario():
        release = runtime(tmp_path, monkeypatch)
        tmp_path.chmod(0o755)
        with pytest.raises(ValueError, match="private_release_directory_required"):
            await release.start()
        tmp_path.chmod(0o700)
        Path(release.socket_path).write_text("owned elsewhere")
        with pytest.raises((ValueError, OSError)):
            await release.start()
        assert Path(release.socket_path).read_text() == "owned elsewhere"
    asyncio.run(scenario())


def test_release_readiness_error_never_leaks_details(tmp_path, monkeypatch):
    def fail():
        raise RuntimeError("private database dsn")
    async def scenario():
        release = runtime(tmp_path, monkeypatch, ready=fail)
        answer = await release.control("activate")
        assert answer["phase"] == "warming" and answer["ready"] is False
        assert "private" not in json.dumps(answer)
    asyncio.run(scenario())


def test_release_drain_tracks_admitted_upload_and_late_ticket(tmp_path, monkeypatch):
    async def scenario():
        pending = [0]
        release = runtime(tmp_path, monkeypatch, pending=lambda: pending[0])
        await release.control("activate")
        entered, finish = asyncio.Event(), asyncio.Event()
        async def upload(scope, receive, send):
            entered.set()
            await finish.wait()
            pending[0] += 1
            await ok(scope, receive, send)
        task = asyncio.create_task(request(release.wrap(upload)))
        await entered.wait()
        status = await release.control("drain")
        assert status["active_requests"] == 1 and status["pending_tickets"] == 0
        finish.set()
        await task
        status = await release.control("status")
        assert status["active_requests"] == 0 and status["pending_tickets"] == 1
    asyncio.run(scenario())


def test_release_counter_cleans_up_failed_response(tmp_path, monkeypatch):
    async def scenario():
        release = runtime(tmp_path, monkeypatch)
        await release.control("activate")
        async def fail(scope, receive, send):
            raise ValueError("synthetic failure")
        with pytest.raises(ValueError):
            await request(release.wrap(fail))
        assert (await release.control("status"))["active_requests"] == 0
    asyncio.run(scenario())


def test_release_socket_closes_when_application_startup_fails(tmp_path, monkeypatch):
    async def scenario():
        release = runtime(tmp_path, monkeypatch)
        async def fail(scope, receive, send):
            assert Path(release.socket_path).exists()
            await send({"type": "lifespan.startup.failed", "message": "synthetic failure"})
        async def receive():
            return {"type": "lifespan.startup"}
        messages = []
        async def send(message):
            messages.append(message)
        await release.wrap(fail)({"type": "lifespan"}, receive, send)
        assert messages[0]["type"] == "lifespan.startup.failed"
        assert not Path(release.socket_path).exists()
    asyncio.run(scenario())


async def socket_control(release, action):
    reader, writer = await asyncio.open_unix_connection(release.socket_path)
    try:
        writer.write(json.dumps({"action": action}).encode() + b"\n")
        await writer.drain()
        return json.loads(await reader.readline())
    finally:
        writer.close()
        await writer.wait_closed()


def test_release_socket_activation_waits_for_successful_application_startup(tmp_path, monkeypatch):
    async def scenario():
        checked = []
        release = runtime(tmp_path, monkeypatch, ready=lambda: checked.append(True) or True, started=False)
        starting, finish, stopping = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def application(scope, receive, send):
            assert (await receive())["type"] == "lifespan.startup"
            starting.set()
            await finish.wait()
            await send({"type": "lifespan.startup.complete"})
            assert (await receive())["type"] == "lifespan.shutdown"
            stopping.set()
            await send({"type": "lifespan.shutdown.complete"})
        commands = asyncio.Queue()
        await commands.put({"type": "lifespan.startup"})
        messages = asyncio.Queue()
        owner = asyncio.create_task(release.wrap(application)({"type": "lifespan"}, commands.get, messages.put))
        try:
            await starting.wait()
            answer = await socket_control(release, "activate")
            assert answer["phase"] == "warming" and answer["ready"] is False
            assert checked == []
            finish.set()
            assert (await messages.get())["type"] == "lifespan.startup.complete"
            assert (await socket_control(release, "activate"))["phase"] == "active"
            await commands.put({"type": "lifespan.shutdown"})
            await stopping.wait()
            await owner
            assert (await release.control("status"))["ready"] is False
        finally:
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
        assert not Path(release.socket_path).exists()
    asyncio.run(scenario())


def test_release_socket_cannot_activate_after_startup_failed_message(tmp_path, monkeypatch):
    async def scenario():
        release = runtime(tmp_path, monkeypatch, started=False)
        failed, finish = asyncio.Event(), asyncio.Event()
        async def application(scope, receive, send):
            await send({"type": "lifespan.startup.failed", "message": "synthetic failure"})
            failed.set()
            await finish.wait()
        async def receive():
            return {"type": "lifespan.startup"}
        async def send(message):
            pass
        owner = asyncio.create_task(release.wrap(application)({"type": "lifespan"}, receive, send))
        try:
            await failed.wait()
            status = await socket_control(release, "activate")
            assert status["ready"] is False and status["phase"] == "warming"
        finally:
            finish.set()
            await owner
        assert not Path(release.socket_path).exists()
    asyncio.run(scenario())
