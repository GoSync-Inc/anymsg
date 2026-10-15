def test_launch_configuration_contains_paths_without_provider_secrets(tmp_path):
    from family_calendar.service.runtime import launch_configuration

    value = launch_configuration(tmp_path, "gateway")
    assert value["Label"] == "com.family-calendar.gateway"
    assert value["ProgramArguments"][-1] == "serve"
    assert value["WorkingDirectory"] == str(tmp_path.resolve())
    assert value["KeepAlive"] is True
    assert "EnvironmentVariables" not in value
    assert not any("dsn" in p or "password" in p or "Bearer" in p for p in value["ProgramArguments"])


def test_checkout_move_keeps_explicit_state_directory(tmp_path):
    from family_calendar.service.runtime import launch_configuration

    state = tmp_path / "persistent"
    before = launch_configuration(tmp_path / "old", "worker", state)
    after = launch_configuration(tmp_path / "new", "worker", state)
    assert before["ProgramArguments"][4] == after["ProgramArguments"][4] == str(state)
    assert before["StandardOutPath"] == after["StandardOutPath"]


def test_unconfigured_worker_fails_without_bootstrapping_a_new_vault(tmp_path):
    import pytest
    from family_calendar.service.cli import run
    from family_calendar.errors import CalendarError

    with pytest.raises(CalendarError, match="existing_runtime_configuration_required"):
        run(["worker"], tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


def test_import_icloud_takes_an_iana_zone_that_discover_keeps(household, tmp_path, monkeypatch):
    import asyncio
    import pytest
    from family_calendar.config import Registry
    from family_calendar.errors import CalendarError
    from family_calendar.private_files import private_dir
    from family_calendar.service import cli

    home = tmp_path / "home"
    legacy = Registry(home)
    legacy.add("apple-test", "synthetic@example.invalid", "synthetic-app-password")
    calendars = [
        {
            "ref": "cal-icloud",
            "href": "https://p01-caldav.icloud.com/1/calendars/test/",
            "label": "Synthetic",
            "privilege": "write",
        }
    ]
    legacy.save_discovery("apple-test", calendars)
    root = private_dir(home / "service")

    def import_icloud(*options):
        argv = ["import-icloud", "--connection", "apple-test", "--owner", "adult-a", *options]
        return cli.dispatch(cli.parser().parse_args(argv), home, root, {}, household)

    def zone():
        row = household.one("SELECT timezone FROM calendars WHERE ref='cal-icloud'")
        return row and row["timezone"]

    with pytest.raises(CalendarError, match="invalid_timezone"):
        import_icloud("--timezone", "Mars/Olympus")
    assert zone() is None
    import_icloud()
    assert zone() == "Asia/Makassar"
    import_icloud("--timezone", "Europe/Berlin")
    assert zone() == "Europe/Berlin"

    class Adapter:
        async def discover(self):
            return calendars

    async def adapter(self, connection):
        return Adapter()

    monkeypatch.setattr(cli.Providers, "adapter", adapter)
    asyncio.run(cli.discover(household, cli.Vault(root / "vault"), "apple-test"))
    assert zone() == "Europe/Berlin"
