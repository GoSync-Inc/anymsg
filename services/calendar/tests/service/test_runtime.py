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
