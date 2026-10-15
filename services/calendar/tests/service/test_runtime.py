def test_launch_configuration_contains_paths_without_provider_secrets(tmp_path):
    from family_calendar.service.runtime import launch_configuration

    value = launch_configuration(tmp_path, "gateway")
    assert value["Label"] == "com.family-calendar.gateway"
    assert value["ProgramArguments"][-1] == "serve"
    assert value["WorkingDirectory"] == str(tmp_path.resolve())
    assert value["KeepAlive"] is True
    assert "EnvironmentVariables" not in value
    assert not any("dsn" in p or "password" in p or "Bearer" in p for p in value["ProgramArguments"])
