import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("publisher_trust", Path(__file__).parents[1] / "scripts/verify_publisher_trust.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("registry", ["pypi", "npm"])
def test_verification_exchanges_only_and_never_returns_tokens(registry):
    requests = []
    def request(url, **kwargs):
        requests.append((url, kwargs))
        return {"value": "synthetic-oidc"} if len(requests) == 1 else {"token": "synthetic-registry"}
    env = {"GITHUB_REPOSITORY_ID": "1418427673", "GITHUB_REF": "refs/heads/main",
           "ACTIONS_ID_TOKEN_REQUEST_URL": "https://issuer.example/token?api=1", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "synthetic-request"}
    result = module.verify(registry, env, request)
    assert result == {"registry": registry, "package": "anymsg", "oidc_exchange": "verified", "published": False}
    assert len(requests) == 2
    assert requests[1][0] == ("https://pypi.org/_/oidc/mint-token" if registry == "pypi" else "https://registry.npmjs.org/-/npm/v1/oidc/token/exchange/package/anymsg")
    assert "synthetic" not in str(result)


@pytest.mark.parametrize("env", [{}, {"GITHUB_REPOSITORY_ID": "1418427673", "GITHUB_REF": "refs/heads/unreviewed"}])
def test_wrong_repository_or_ref_rejected_before_credential_access(env):
    with pytest.raises(RuntimeError):
        module.verify("npm", env, lambda *a, **k: pytest.fail("must not request identity"))


def test_redirect_and_insecure_transport_are_rejected():
    with pytest.raises(RuntimeError):
        module.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example")
    with pytest.raises(ValueError):
        module.request_json("http://issuer.example")
