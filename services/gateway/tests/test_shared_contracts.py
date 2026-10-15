import json

import pytest

from platform_contracts import Capability, ReceiptReference, decode, safe_error
from agent_gateway.adapters.calls import receipt_reference


def test_version_one_json_roundtrip_and_unknown_versions_fail_closed():
    for value in [
        Capability("calls", "get_call_result", 4),
        ReceiptReference("calendar", "apply", "operation-1", "request-1"),
    ]:
        assert decode(type(value), json.dumps(value.to_dict())) == value
        raw = {**value.to_dict(), "schema_version": 2}
        with pytest.raises(ValueError, match="unsupported"):
            decode(type(value), json.dumps(raw))
    assert safe_error("mutation_outcome_unknown", unknown_outcome=True)["automatic_retry"] is False
    with pytest.raises(ValueError):
        safe_error("private token or arbitrary exception")


def test_calls_reference_does_not_wrap_or_drop_domain_payload():
    result = {"id": "call-1", "brief": {"goal": "Synthetic"}, "unknown_domain_extension": {"a": 1}}
    assert receipt_reference("get_call_result", result).receipt_id == "call-1"
    assert result == {"id": "call-1", "brief": {"goal": "Synthetic"}, "unknown_domain_extension": {"a": 1}}
