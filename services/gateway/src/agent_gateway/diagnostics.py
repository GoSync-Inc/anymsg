"""Bounded protocol diagnostics; backend source text is never error guidance."""
import hashlib
import json
import re

from .catalog import CATALOG, public


NEXT_DISCOVERY_ACTION = "refresh_connection_if_tools_list_differs"
BACKEND_ERROR_CODES = frozenset({
    "query_timeout", "service_busy", "service_unavailable", "temporarily_unavailable", "subscription_wait_timeout",
})
REQUEST_ID = re.compile(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})\Z")


def digest(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def discovery(visible, actions, revision):
    """Fingerprint the effective reviewed schemas, including narrowed action enums."""
    return {
        "tool_count": len(visible),
        "tools_sha256": digest([public(CATALOG[name], actions) for name in sorted(visible)]),
        "policy_revision": revision,
        "notifications_supported": False,
        "next_action": NEXT_DISCOVERY_ACTION,
    }


def manifest_summary(capabilities):
    """Keep catalog provenance without advertising unselected backend operations."""
    manifest = capabilities.get("tool_manifest")
    if not isinstance(manifest, dict):
        return None
    result = {}
    version = manifest.get("version")
    if isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", version):
        result["version"] = version
    names = manifest.get("names")
    if (isinstance(names, list) and len(names) <= 10000
            and all(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_]{1,128}", name) for name in names)):
        names = sorted(set(names))
        result.update(tool_count=len(names), names_sha256=digest(names))
    if manifest.get("discovery_action") == NEXT_DISCOVERY_ACTION:
        result["discovery_action"] = NEXT_DISCOVERY_ACTION
    return result or None


def failure_details(status, value, header_request_id=None):
    result = {"error": "backend_unavailable", "backend_status": status}
    value = value if isinstance(value, dict) else {}
    code = value.get("error")
    if isinstance(code, str) and code in BACKEND_ERROR_CODES:
        result["backend_error"] = code
    # Messaging issues a random hex UUID. Accept UUID encodings only, never an
    # arbitrary error body/header that happens to be labelled a request ID.
    for request_id in (value.get("request_id"), header_request_id):
        if isinstance(request_id, str) and REQUEST_ID.fullmatch(request_id):
            result["request_id"] = request_id
            break
    return result


CONSUMPTION_OPERATIONS = frozenset({
    "fetch_subscription_events", "ack_subscription_events", "wait_for_subscription_events",
    "configure_subscription_delivery", "test_subscription", "submit_feedback", "list_feedback",
})
CONSUMPTION_ERRORS = frozenset({
    "unauthorized", "forbidden", "not_found", "invalid_limit", "invalid_version", "invalid_receipt",
    "stale_ack", "idempotency_conflict", "invalid_parameters", "invalid_filter", "invalid_subscription",
    "subscription_access_changed", "subscription_closed", "subscription_conflict", "subscription_limit",
    "subscription_receipt_limit", "invalid_feedback", "feedback_limit", "invalid_receiver",
    "delivery_not_configured", "rate_limited", "invalid_request_id",
})


def consumption_failure(status, value, header_request_id=None):
    result = failure_details(status, value, header_request_id)
    result.pop("backend_error", None)
    code = value.get("error")
    result["error"] = code if isinstance(code, str) and code in CONSUMPTION_ERRORS else "backend_denied"
    return result
