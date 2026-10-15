import json
import secrets
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google_auth_oauthlib.flow import InstalledAppFlow

from family_calendar.errors import CalendarError
from family_calendar.private_files import read_private, write_private

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.app.created",
]


def validate_identity(claims, nonce, expected_email):
    if (
        not claims.get("sub")
        or claims.get("nonce") != nonce
        or claims.get("email_verified") is not True
        or claims.get("email", "").lower() != expected_email.lower()
    ):
        raise CalendarError("oauth_identity_mismatch")
    return claims["sub"]


def connect(client_file, vault, connection_id, expected_email, authorization_file):
    config = json.loads(read_private(Path(client_file)))
    if set(config) != {"installed"}:
        raise CalendarError("desktop_oauth_client_required")
    # Never load existing gws/Codex tokens. This is a new flow with its own state, PKCE and nonce.
    nonce = secrets.token_urlsafe(32)
    flow = InstalledAppFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
    original = flow.authorization_url

    def authorization_url(**kwargs):
        url, state = original(nonce=nonce, prompt="consent", login_hint=expected_email, **kwargs)
        write_private(Path(authorization_file), url.encode())
        return url, state

    flow.authorization_url = authorization_url
    try:
        credentials = flow.run_local_server(
            host="localhost",
            bind_addr="127.0.0.1",
            port=0,
            open_browser=False,
            authorization_prompt_message="",
            timeout_seconds=3600,
            success_message="Family Calendar Service connected. You can close this tab.",
        )
        claims = id_token.verify_oauth2_token(
            credentials.id_token, Request(), config["installed"]["client_id"]
        )
        sub = validate_identity(claims, nonce, expected_email)
        if not credentials.refresh_token or not credentials.has_scopes(SCOPES):
            raise CalendarError("oauth_scopes_incomplete")
        vault.save(
            connection_id,
            {
                "provider": "google",
                "sub": sub,
                "email": expected_email,
                "credentials": json.loads(credentials.to_json()),
            },
        )
        return {"connection_id": connection_id, "identity_verified": True, "saved": True}
    except CalendarError:
        raise
    except Exception:
        raise CalendarError("oauth_authorization_failed") from None
    finally:
        Path(authorization_file).unlink(missing_ok=True)
