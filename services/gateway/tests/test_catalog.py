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
