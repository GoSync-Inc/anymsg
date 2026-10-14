import base64
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

from family_calendar.errors import CalendarError
from family_calendar.contracts import validate_intent
from family_calendar.private_files import private_dir, read_private, write_private


def encode_intent(intent):
    value = dict(intent)
    for key in ("desired", "before"):
        if value.get(key) is not None:
            value[key] = base64.b64encode(value[key]).decode()
    return json.dumps(value, sort_keys=True)


def decode_intent(value):
    intent = json.loads(value)
    for key in ("desired", "before"):
        if intent.get(key) is not None:
            intent[key] = base64.b64decode(intent[key])
    return intent


class Journal:
    def __init__(self, root, connection_id, calendar_ref):
        self.root = private_dir(root)
        scope_path = self.root / "scope.json"
        scope = {"schema_version": 1, "connection_id": connection_id, "calendar_ref": calendar_ref}
        if scope_path.exists() or scope_path.is_symlink():
            if json.loads(read_private(scope_path)) != scope:
                raise CalendarError("run_scope_mismatch")
        else:
            write_private(scope_path, json.dumps(scope).encode())
        path = self.root / "journal.sqlite"
        if path.is_symlink():
            raise CalendarError("unsafe_private_path")
        if path.exists():
            read_private(path)
        else:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS steps (
            id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, payload_hash TEXT NOT NULL,
            intent TEXT NOT NULL, state TEXT NOT NULL, etag TEXT, error_code TEXT,
            observed_data BLOB, observed_hash TEXT, updated_at TEXT NOT NULL)""")
        self.db.commit()

    def close(self):
        self.db.close()

    def prepare(self, request_id, intent):
        validate_intent(intent)
        payload = encode_intent(intent)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.db:
            existing = self.db.execute(
                "SELECT id,payload_hash FROM steps WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing:
                if existing["payload_hash"] != digest:
                    raise CalendarError("idempotency_conflict")
                return existing["id"]
            row = self.db.execute(
                "INSERT INTO steps(request_id,payload_hash,intent,state,updated_at) VALUES(?,?,?,'prepared',?)",
                (request_id, digest, payload, datetime.now(timezone.utc).isoformat()),
            )
            return row.lastrowid

    def find(self, request_id):
        row = self.db.execute("SELECT id FROM steps WHERE request_id=?", (request_id,)).fetchone()
        return self.get(row["id"]) if row else None

    def get(self, step_id):
        row = self.db.execute("SELECT * FROM steps WHERE id=?", (step_id,)).fetchone()
        if row is None:
            raise CalendarError("operation_not_found")
        value = dict(row)
        value["intent"] = decode_intent(value["intent"])
        return value

    def mark(self, step_id, state, *, etag=None, error=None, observed=None, observed_hash=None):
        if state not in {
            "prepared",
            "sending",
            "verified",
            "unknown",
            "conflict",
            "failed",
            "needs_attention",
        }:
            raise CalendarError("invalid_operation_state")
        with self.db:
            self.db.execute(
                "UPDATE steps SET state=?,etag=?,error_code=?,observed_data=?,observed_hash=?,updated_at=? WHERE id=?",
                (
                    state,
                    etag,
                    error,
                    observed,
                    observed_hash,
                    datetime.now(timezone.utc).isoformat(),
                    step_id,
                ),
            )

    def claim(self, step_id):
        with self.db:
            row = self.db.execute(
                "UPDATE steps SET state='sending',updated_at=? WHERE id=? AND state='prepared'",
                (datetime.now(timezone.utc).isoformat(), step_id),
            )
            return row.rowcount == 1

    def steps(self):
        return [self.get(row[0]) for row in self.db.execute("SELECT id FROM steps ORDER BY id")]

    def owned_objects(self):
        created = {}
        for step in self.steps():
            intent = step["intent"]
            if intent["kind"] == "create" and step["state"] == "verified":
                created[intent["href"]] = {
                    "href": intent["href"],
                    "etag": step["etag"],
                    "desired": intent["desired"],
                }
            elif intent["href"] in created and step["state"] == "verified":
                if intent["kind"] == "delete":
                    del created[intent["href"]]
                else:
                    created[intent["href"]].update(etag=step["etag"], desired=intent["desired"])
        return list(created.values())
