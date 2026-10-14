from pathlib import Path
import pytest
from family_calendar.errors import CalendarError


def test_proxy_rejects_nonlocal_endpoint_before_reading_key(tmp_path):
    from family_calendar.service.proxy import proxy_config

    for url in (
        "https://remote.example/mcp",
        "http://127.0.0.1:13232/mcp?token=x",
        "http://localhost.evil:13232/mcp",
        "http://user@localhost:13232/mcp",
    ):
        with pytest.raises(CalendarError, match="unsafe_mcp_endpoint"):
            proxy_config(url, tmp_path / "missing.key")


def test_proxy_uses_private_key_without_credentials_in_arguments(tmp_path):
    from family_calendar.service.proxy import proxy_config
    from family_calendar.private_files import write_private

    path = Path(tmp_path) / "private" / "agent.key"
    write_private(path, b"canary-secret")
    url, key = proxy_config("http://127.0.0.1:13232/mcp", path)
    assert url == "http://127.0.0.1:13232/mcp"
    assert key == "canary-secret"
    path.chmod(0o644)
    with pytest.raises(CalendarError, match="unsafe_private_permissions"):
        proxy_config(url, path)
