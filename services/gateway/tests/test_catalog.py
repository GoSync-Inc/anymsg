from copy import deepcopy

import pytest

from agent_gateway.catalog import CATALOG, load_catalog, arguments


def test_duplicate_catalog_names_and_reserved_name_rejected():
    tool = deepcopy(CATALOG["calendar_calendars"])
    with pytest.raises(ValueError, match="collision"):
        load_catalog([tool, tool])
    tool["name"] = "platform_capabilities"
    with pytest.raises(ValueError, match="collision"):
        load_catalog([tool])


def test_schema_defaults_do_not_accept_unknown_arguments():
    tool = CATALOG["messages_search"]
    with pytest.raises(ValueError):
        arguments(tool, {"query": "synthetic", "owner_override": True})
    assert "owner_override" not in arguments(tool, {"query": "synthetic"})


READ = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}


def test_every_tool_declares_hints_and_reads_are_never_destructive():
    for tool in CATALOG.values():
        hints = tool["annotations"]
        assert {"readOnlyHint", "destructiveHint", "openWorldHint"} <= set(hints), tool["name"]
        assert not (hints["readOnlyHint"] and hints["destructiveHint"]), tool["name"]


def test_calendar_reads_and_local_plan_are_not_presented_as_provider_writes():
    for name in ("capabilities", "calendars", "members", "agenda", "slots", "operation", "assignments"):
        assert CATALOG["calendar_" + name]["annotations"] == READ
    assert CATALOG["calendar_plan"]["annotations"] == {
        "readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False,
    }
    for name in ("apply", "assignment"):
        assert CATALOG["calendar_" + name]["annotations"] == {
            "readOnlyHint": False, "destructiveHint": True, "openWorldHint": True,
        }
