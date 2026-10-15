"""Live acceptance, never inferred from a library or a successful mock."""


def capabilities(checks, gate, *, live, native_visual_pass):
    core = gate in {"accepted_core", "accepted_core_and_future"}
    result = {
        name: {"status": "verified" if live and core else "unverified", "evidence": "gate_a_core"}
        for name in (
            "read",
            "create",
            "update",
            "delete",
            "conditional_write",
            "recurrence_single",
            "recurrence_series",
        )
    }
    result["recurrence_future"] = {
        "status": "verified" if live and gate == "accepted_core_and_future" else "unverified",
        "evidence": "gate_a_future",
        "scope": "simple_weekly_preserve_absolute",
    }
    result["native_range"] = {
        "status": "verified"
        if live
        and core
        and native_visual_pass
        and checks.get("native_range_probe", {}).get("status") == "server_verified"
        else "unverified",
        "evidence": "server_readback_and_client_visual_check",
    }
    for name in ("shared_calendar_write", "multiaccount_live", "incremental_sync"):
        result[name] = {"status": "unverified"}
    for name in ("invitations", "floating_availability"):
        result[name] = {"status": "unsupported"}
    return result
