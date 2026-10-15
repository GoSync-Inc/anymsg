from family_calendar.cli import main


def test_help_and_no_password_argument(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "connection" in out and "pilot" in out
    assert main(["connection", "add-icloud", "--help"]) == 0
    out = capsys.readouterr().out
    assert "--password" not in out


def test_unconfigured_connection_is_fixed_error_without_traceback(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "pilot", "icloud", "discover", "--connection", "missing"]) == 1
    out = capsys.readouterr().out
    assert "connection_not_found" in out
    assert "Traceback" not in out


def test_unexpected_exception_cannot_expose_credentials(tmp_path, capsys, monkeypatch):
    from family_calendar.config import Registry

    def fail(*args):
        raise RuntimeError("credential-value-from-provider")

    monkeypatch.setattr(Registry, "load", fail)
    assert main(["--home", str(tmp_path), "pilot", "icloud", "discover", "--connection", "a"]) == 1
    out = capsys.readouterr().out
    assert "unexpected_error" in out
    assert "credential-value-from-provider" not in out
    assert "Traceback" not in out
