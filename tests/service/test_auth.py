import pytest

from family_calendar.errors import CalendarError


def test_identity_nonce_and_account_binding():
    from family_calendar.providers.google.auth import validate_identity

    claims = {"sub": "account-a", "email": "owner@example.test", "email_verified": True, "nonce": "nonce-a"}
    assert validate_identity(claims, "nonce-a", "owner@example.test") == "account-a"
    for nonce, email in [("nonce-b", "owner@example.test"), ("nonce-a", "other@example.test")]:
        with pytest.raises(CalendarError, match="oauth_identity_mismatch"):
            validate_identity(claims, nonce, email)


def test_secret_store_separates_accounts_and_does_not_echo(tmp_path):
    from family_calendar.service.vault import Vault

    vault = Vault(tmp_path / "vault")
    vault.save("google-a", {"refresh_token": "canary-a"})
    vault.save("google-b", {"refresh_token": "canary-b"})
    assert vault.load("google-a")["refresh_token"] == "canary-a"
    assert vault.load("google-b")["refresh_token"] == "canary-b"
    assert "canary" not in (tmp_path / "vault" / "google-a.enc").read_text()
    assert (tmp_path / "vault" / "google-a.enc").stat().st_mode & 0o777 == 0o600
    with pytest.raises(CalendarError, match="secret_not_found"):
        vault.load("google-c")


def test_existing_connection_cannot_silently_change_provider_identity(tmp_path):
    from family_calendar.service.vault import Vault

    vault = Vault(tmp_path / "vault")
    vault.save("google-a", {"provider": "google", "sub": "a", "credentials": {"token": "canary-a"}})
    with pytest.raises(CalendarError, match="connection_identity_changed"):
        vault.save("google-a", {"provider": "google", "sub": "b", "credentials": {"token": "canary-b"}})
    with pytest.raises(CalendarError, match="connection_identity_changed"):
        vault.save(
            "google-a", {"provider": "icloud", "username": "another@example.test", "password": "canary-b"}
        )
    assert vault.load("google-a")["sub"] == "a"
