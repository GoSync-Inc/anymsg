import json
from cryptography.fernet import Fernet, InvalidToken
from family_calendar.config import Registry
from family_calendar.errors import CalendarError
from family_calendar.private_files import private_dir, read_private, write_private


class Vault:
    """Owner/worker-only secrets. Never passed to the HTTP gateway."""

    def __init__(self, root):
        self.root = private_dir(root)

    def _cipher(self):
        path = self.root / "key"
        if not path.exists():
            if list(self.root.glob("*.enc")):
                raise CalendarError("secret_key_missing")
            write_private(path, Fernet.generate_key())
        return Fernet(read_private(path))

    def save(self, connection_id, value):
        Registry.validate_id(connection_id)
        if (self.root / (connection_id + ".enc")).exists():
            previous = self.load(connection_id)
            field = "sub" if previous.get("provider") == "google" else "username"
            if previous.get("provider") and (
                previous["provider"] != value.get("provider") or previous.get(field) != value.get(field)
            ):
                raise CalendarError("connection_identity_changed")
        write_private(
            self.root / (connection_id + ".enc"), self._cipher().encrypt(json.dumps(value).encode())
        )

    def load(self, connection_id):
        Registry.validate_id(connection_id)
        path = self.root / (connection_id + ".enc")
        if not path.exists():
            raise CalendarError("secret_not_found")
        try:
            return json.loads(self._cipher().decrypt(read_private(path)))
        except (InvalidToken, ValueError):
            raise CalendarError("credentials_unreadable") from None
