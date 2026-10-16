"""A generation-bound messaging connection, separate from live access policy."""
import os


def messaging_dial_url(release, deployment):
    name = "DEEDKIT_RELEASE_MESSAGING_URL"
    if name not in os.environ:
        return None
    expected = "http://http-api-" + release.generation + ":8080" if release else None
    if (expected is None or os.environ[name] != expected
            or "messaging" not in deployment["modules"]):
        raise ValueError("invalid_release_messaging_url")
    return expected
