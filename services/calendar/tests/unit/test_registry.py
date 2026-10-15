import os

import pytest

from family_calendar.config import Registry
from family_calendar.errors import CalendarError


def test_connections_keep_separate_encrypted_credentials(tmp_path):
    reg = Registry(tmp_path / "private")
    reg.add("a", "one@example.invalid", "secret-one")
    reg.add("b", "two@example.invalid", "secret-two")
    assert reg.load("a").password == "secret-one"
    assert reg.load("b").username == "two@example.invalid"
    for file in (tmp_path / "private").rglob("*"):
        if file.is_file():
            assert "secret-one" not in file.read_text(errors="replace")
            assert "one@example.invalid" not in file.read_text(errors="replace")
            assert file.stat().st_mode & 0o777 == 0o600
    assert "secret-one" not in repr(reg.load("a"))


def test_existing_connection_cannot_be_silently_overwritten(tmp_path):
    reg = Registry(tmp_path)
    reg.add("a", "one@example.invalid", "secret")
    with pytest.raises(CalendarError, match="connection_exists"):
        reg.add("a", "two@example.invalid", "other")


def test_registry_rejects_path_traversal_and_symlinks(tmp_path):
    reg = Registry(tmp_path / "private")
    with pytest.raises(CalendarError, match="invalid_connection_id"):
        reg.add("../outside", "a", "b")
    (tmp_path / "private" / "connections.json").symlink_to(tmp_path / "outside")
    with pytest.raises(CalendarError, match="unsafe_private_path"):
        reg.add("a", "a", "b")


def test_write_allowlist_is_explicit_and_connection_scoped(tmp_path):
    reg = Registry(tmp_path)
    reg.add("a", "one@example.invalid", "secret")
    reg.add("b", "two@example.invalid", "secret")
    reg.save_discovery(
        "a",
        [
            {
                "ref": "cal1",
                "href": "https://caldav.icloud.com/a/cal/",
                "label": "Pilot",
                "privilege": "unknown",
            }
        ],
    )
    with pytest.raises(CalendarError, match="write_not_enabled"):
        reg.require_write("a", "cal1")
    reg.allow_calendar("a", "cal1")
    assert reg.require_write("a", "cal1")["ref"] == "cal1"
    with pytest.raises(CalendarError, match="calendar_not_found"):
        reg.require_write("b", "cal1")


def test_registry_does_not_accept_world_readable_key(tmp_path):
    reg = Registry(tmp_path)
    reg.add("a", "one@example.invalid", "secret")
    os.chmod(tmp_path / "master.key", 0o644)
    with pytest.raises(CalendarError, match="unsafe_private_permissions"):
        reg.load("a")


def test_lost_key_is_not_silently_regenerated(tmp_path):
    reg = Registry(tmp_path)
    reg.add("a", "a@example.invalid", "secret")
    (tmp_path / "master.key").unlink()
    with pytest.raises(CalendarError, match="secret_key_missing"):
        reg.load("a")
    assert not (tmp_path / "master.key").exists()
