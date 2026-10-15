from dataclasses import dataclass, field
import json
import re

from cryptography.fernet import Fernet, InvalidToken

from .errors import CalendarError
from .private_files import private_dir, read_private, write_private


@dataclass(frozen=True)
class Connection:
    id: str
    username: str = field(repr=False)
    password: str = field(repr=False)


class Registry:
    """Local single-operator registry; no shared service or agent API yet."""

    def __init__(self, root):
        self.root = private_dir(root)
        self.path = self.root / "connections.json"

    def _data(self):
        if not self.path.exists() and not self.path.is_symlink():
            return {"schema_version": 1, "connections": {}}
        try:
            return json.loads(read_private(self.path))
        except (ValueError, KeyError):
            raise CalendarError("invalid_private_configuration") from None

    def _save(self, data):
        write_private(self.path, json.dumps(data, indent=2).encode())

    def _fernet(self):
        path = self.root / "master.key"
        if not path.exists() and not path.is_symlink():
            if self._data()["connections"]:
                raise CalendarError("secret_key_missing")
            write_private(path, Fernet.generate_key())
        try:
            return Fernet(read_private(path))
        except ValueError:
            raise CalendarError("invalid_secret_key") from None

    @staticmethod
    def validate_id(connection_id):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", connection_id):
            raise CalendarError("invalid_connection_id")

    def add(self, connection_id, username, password):
        self.validate_id(connection_id)
        if not username.strip() or not password.strip():
            raise CalendarError("empty_credentials")
        data = self._data()
        if connection_id in data["connections"]:
            raise CalendarError("connection_exists")
        encrypted = self._fernet().encrypt(
            json.dumps({"username": username.strip(), "password": password.strip()}).encode()
        )
        data["connections"][connection_id] = {
            "credentials": encrypted.decode(),
            "calendars": [],
            "write_allowlist": [],
        }
        self._save(data)

    def load(self, connection_id):
        self.validate_id(connection_id)
        entry = self._entry(connection_id)
        try:
            value = json.loads(self._fernet().decrypt(entry["credentials"].encode()))
            return Connection(connection_id, value["username"], value["password"])
        except (ValueError, KeyError, InvalidToken):
            raise CalendarError("credentials_unreadable") from None

    def _entry(self, connection_id):
        self.validate_id(connection_id)
        entry = self._data()["connections"].get(connection_id)
        if entry is None:
            raise CalendarError("connection_not_found")
        return entry

    def list_connections(self):
        return [
            {
                "id": key,
                "calendars": len(value["calendars"]),
                "write_calendars": len(value["write_allowlist"]),
            }
            for key, value in self._data()["connections"].items()
        ]

    def save_discovery(self, connection_id, calendars):
        self._entry(connection_id)
        data = self._data()
        data["connections"][connection_id]["calendars"] = calendars
        # Do not inherit write access if discovery changes a ref's target.
        old = {c["ref"]: c["href"] for c in self._entry(connection_id)["calendars"]}
        data["connections"][connection_id]["write_allowlist"] = [
            ref
            for ref in data["connections"][connection_id]["write_allowlist"]
            if any(c["ref"] == ref and c["href"] == old.get(ref) for c in calendars)
        ]
        self._save(data)

    def calendar(self, connection_id, ref):
        found = next((c for c in self._entry(connection_id)["calendars"] if c["ref"] == ref), None)
        if found is None:
            raise CalendarError("calendar_not_found")
        return found

    def allow_calendar(self, connection_id, ref):
        calendar = self.calendar(connection_id, ref)
        if calendar["privilege"] == "read_only":
            raise CalendarError("provider_readonly")
        data = self._data()
        allowed = data["connections"][connection_id]["write_allowlist"]
        if ref not in allowed:
            allowed.append(ref)
        self._save(data)

    def disable_write(self, connection_id, ref):
        self._entry(connection_id)
        data = self._data()
        allowed = data["connections"][connection_id]["write_allowlist"]
        if ref in allowed:
            allowed.remove(ref)
        self._save(data)

    def require_write(self, connection_id, ref):
        calendar = self.calendar(connection_id, ref)
        if ref not in self._entry(connection_id)["write_allowlist"]:
            raise CalendarError("write_not_enabled")
        if calendar["privilege"] == "read_only":
            raise CalendarError("provider_readonly")
        return calendar
