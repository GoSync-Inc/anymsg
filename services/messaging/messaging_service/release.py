"""Passive schema checks for an opt-in HTTP replacement; never a migration."""
import hashlib
from pathlib import Path


def migration_inventory():
    return {int(path.name[:3]): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(Path(__file__).with_name("migrations").glob("[0-9][0-9][0-9]_*.sql"))}


def schema_inventory():
    migrations = migration_inventory()
    return {"schema_sha256": hashlib.sha256(Path(__file__).with_name("schema.sql").read_bytes()).hexdigest(),
            "migrations": {str(version): sha for version, sha in migrations.items()},
            "version": max(migrations)}


def readiness(store):
    """Compare both the schema version and complete applied checksum inventory."""
    try:
        expected = migration_inventory()
        with store.pool.connection(timeout=1) as connection:
            with connection.transaction():
                connection.execute("SET TRANSACTION READ ONLY")
                connection.execute("SET LOCAL statement_timeout='1s'")
                connection.execute("SET LOCAL transaction_timeout='1500ms'")
                row = connection.execute(
                    "SELECT value FROM probe_metadata WHERE name='schema_version'").fetchone()
                if row is None or int(row["value"]) != max(expected):
                    return False
                rows = connection.execute("SELECT version,sha256 FROM archive_migrations ORDER BY version").fetchall()
                return {row["version"]: row["sha256"] for row in rows} == expected
    except Exception:
        return False
