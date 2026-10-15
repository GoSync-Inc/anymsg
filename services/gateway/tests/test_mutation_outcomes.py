import json

import httpx
from starlette.testclient import TestClient

from agent_gateway.app import create_app
from test_policy import config


def test_timeout_after_mutation_dispatch_is_unknown_and_never_retried(tmp_path):
    value = config()
    key = tmp_path / "calendar.key"
    key.write_text("backend-calendar-a")
    key.chmod(0o600)
    value["principals"][0]["credentials"]["calendar"]["key_file"] = str(key)
    value["principals"][0]["grants"]["calendar"] = ["plan:execute"]
    value["profiles"]["reader"]["tools"] = ["calendar_apply"]
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    attempts = []

    def handler(request):
        if request.url.path == "/v1/capabilities":
            return httpx.Response(200, json={"permissions": ["plan:execute"]})
        attempts.append(json.loads(request.content))
        raise httpx.ReadTimeout("synthetic ambiguous receipt")

    app = create_app(path, "http://testserver", transports={"calendar": httpx.MockTransport(handler)})
    with TestClient(app) as client:
        args = {"plan_id": "synthetic-plan", "request_id": "synthetic-request"}
        response = client.post("/v1/calendar_apply", json=args, headers={"Authorization": "Bearer gateway-a"})
        assert response.status_code == 503
        assert response.json() == {
            "schema_version": 1,
            "error": "mutation_outcome_unknown",
            "outcome": "unknown",
            "automatic_retry": False,
            "reason": "gateway_timeout",
        }
        assert attempts == [args]
        value["revision"] += 1
        value["principals"][0]["grants"]["calendar"] = []
        path.write_text(json.dumps(value))
        assert (
            client.post(
                "/v1/calendar_apply", json=args, headers={"Authorization": "Bearer gateway-a"}
            ).status_code
            == 403
        )
        assert attempts == [args]
