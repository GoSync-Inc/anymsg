#!/usr/bin/env python3
"""Exchange this release workflow's OIDC identity; never upload a distribution."""
import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("OIDC endpoint redirected; verification refused")


def request_json(url, *, headers=None, payload=None, post=False):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise ValueError("OIDC endpoint must use HTTPS")
    data = json.dumps(payload).encode() if payload is not None else (b"" if post else None)
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            body = response.read(65537)
            if len(body) > 65536:
                raise RuntimeError("OIDC response exceeded limit")
            return json.loads(body)
    except urllib.error.HTTPError as error:
        # Registry response bodies may contain credential/claim data.
        raise RuntimeError(f"OIDC request failed: HTTP {error.code}") from None


def verify(registry, env, request=request_json):
    if env.get("GITHUB_REPOSITORY_ID") != "1418427673" or env.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Verification requires the existing repository's main branch")
    audience = "pypi" if registry == "pypi" else "npm:registry.npmjs.org"
    issuer = env["ACTIONS_ID_TOKEN_REQUEST_URL"]
    separator = "&" if "?" in issuer else "?"
    oidc = request(issuer + separator + urllib.parse.urlencode({"audience": audience}),
                   headers={"Authorization": "Bearer " + env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})["value"]
    if registry == "pypi":
        result = request("https://pypi.org/_/oidc/mint-token", payload={"token": oidc})
    else:
        result = request("https://registry.npmjs.org/-/npm/v1/oidc/token/exchange/package/anymsg",
                         headers={"Authorization": "Bearer " + oidc}, post=True)
    if not isinstance(result.get("token"), str) or not result["token"]:
        raise RuntimeError("Registry did not return a credential")
    # Discard the token: this path cannot publish, persist it or pass it to another job.
    return {"registry": registry, "package": "anymsg", "oidc_exchange": "verified", "published": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", choices=("pypi", "npm"))
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.registry, os.environ)))
    except Exception as error:
        # No traceback, request URL, response body or secret on failure.
        raise SystemExit("Publisher trust verification failed (" + type(error).__name__ + ")") from None
