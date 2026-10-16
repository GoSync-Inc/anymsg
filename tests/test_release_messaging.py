"""Read-only schema admission; no production database or provider sessions."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import tempfile
from types import SimpleNamespace


class Connection:
    def __init__(self, version, inventory):
        self.version, self.inventory, self.statements = version, inventory, []
    @contextmanager
    def transaction(self):
        yield
    def execute(self, sql):
        self.statements.append(sql)
        return self
    def fetchone(self):
        return {"value": str(self.version)}
    def fetchall(self):
        return [{"version": k, "sha256": v} for k, v in self.inventory.items()]


def store(connection):
    @contextmanager
    def connect(**kwargs):
        assert kwargs == {"timeout": 1}
        yield connection
    return SimpleNamespace(pool=SimpleNamespace(connection=connect))


def test_messaging_release_readiness_requires_exact_shipped_schema_read_only():
    from messaging_service.release import migration_inventory, readiness
    inventory = migration_inventory()
    connection = Connection(max(inventory), inventory)
    assert readiness(store(connection)) is True
    statements = " ".join(connection.statements).lower()
    assert "read only" in statements and "statement_timeout" in statements
    assert all(word not in statements for word in ("insert ", "update ", "create ", "alter "))
    assert readiness(store(Connection(max(inventory) - 1, inventory))) is False
    assert readiness(store(Connection(max(inventory), {**inventory, 2: "bad"}))) is False
    assert readiness(store(Connection(max(inventory), {**inventory, 999: "bad"}))) is False


def test_messaging_warming_startup_does_not_sweep_uploads(tmp_path, monkeypatch):
    from messaging_service.app import create_app
    calls = []
    fixture = SimpleNamespace(public_url="http://testserver",
        cleanup_file_uploads=lambda: calls.append("cleanup"))
    with tempfile.TemporaryDirectory(prefix="dkm-", dir=str(Path("/tmp").resolve())) as directory:
        monkeypatch.setenv("DEEDKIT_RELEASE_ID", "012345abcdef")
        monkeypatch.setenv("DEEDKIT_RELEASE_SHA", "a" * 40)
        monkeypatch.setenv("DEEDKIT_RELEASE_SOCKET", directory + "/control.sock")
        app = create_app(fixture, ["testserver"])
        async def check():
            async with app.app.router.lifespan_context(app.app):
                await asyncio.sleep(.01)
                assert calls == []
                assert app.release.phase == "warming"
        asyncio.run(check())


def test_messaging_nonrelease_startup_retains_upload_maintenance(monkeypatch):
    from messaging_service.app import create_app
    for key in ("ID", "SHA", "SOCKET"):
        monkeypatch.delenv("DEEDKIT_RELEASE_" + key, raising=False)
    calls = []
    fixture = SimpleNamespace(public_url="http://testserver",
        cleanup_file_uploads=lambda: calls.append("cleanup"))
    app = create_app(fixture, ["testserver"])
    async def check():
        async with app.app.router.lifespan_context(app.app):
            for _ in range(100):
                if calls:
                    break
                await asyncio.sleep(.001)
            assert calls == ["cleanup"]
    asyncio.run(check())


def test_messaging_release_readiness_reads_real_postgresql_without_mutation(database):
    from messaging_service.release import migration_inventory, readiness
    expected = migration_inventory()
    with database.pool.connection() as connection:
        before = connection.execute(
            "SELECT version,sha256,applied_at FROM archive_migrations ORDER BY version").fetchall()
        version_before = connection.execute(
            "SELECT value FROM probe_metadata WHERE name='schema_version'").fetchone()
    assert readiness(database) is True
    with database.pool.connection() as connection:
        assert connection.execute(
            "SELECT version,sha256,applied_at FROM archive_migrations ORDER BY version").fetchall() == before
        assert connection.execute(
            "SELECT value FROM probe_metadata WHERE name='schema_version'").fetchone() == version_before
        assert {row['version']: row['sha256'] for row in before} == expected
