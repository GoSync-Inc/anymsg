"""Reviewed, packaged schemas. New backend methods never appear automatically."""

from copy import deepcopy
import base64
import binascii
from importlib.resources import files
import json

from jsonschema import Draft202012Validator


def load_catalog(rows):
    result = {}
    for tool in rows:
        if tool["name"] in result or tool["name"] == "platform_capabilities":
            raise ValueError("catalog name collision")
        Draft202012Validator.check_schema(tool["inputSchema"])
        result[tool["name"]] = tool
    return result


CATALOG = load_catalog(json.loads(files(__package__).joinpath("catalog.json").read_text()))
ACTIONS = json.loads(files(__package__).joinpath("actions.json").read_text())
CATALOG["platform_capabilities"] = {
    "name": "platform_capabilities",
    "module": "gateway",
    "operation": "capabilities",
    "scopes": [],
    "description": "Selected modules and tools for this agent. Backend data coverage remains explicit/unknown.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
}


def arguments(tool, value):
    if not isinstance(value, dict):
        raise ValueError("invalid_arguments")
    schema = tool["inputSchema"]
    if next(Draft202012Validator(schema).iter_errors(value), None) is not None:
        raise ValueError("invalid_arguments")
    if tool["operation"] == "upload_file_chunk":
        try:
            raw = base64.b64decode(value["data_base64"], validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("invalid_arguments") from None
        if not 0 < len(raw) <= 262144:
            raise ValueError("invalid_arguments")
    result = deepcopy(value)
    # REST and MCP backend defaults differ for conversation tools. Preserve the
    # reviewed MCP defaults when routing either gateway transport over REST.
    for key, prop in schema.get("properties", {}).items():
        if key not in result and "default" in prop:
            result[key] = deepcopy(prop["default"])
    return result


def public(tool, actions=None):
    result = {
        k: deepcopy(v)
        for k, v in tool.items()
        if k in {"name", "title", "description", "inputSchema", "outputSchema", "annotations", "_meta"}
    }
    if tool.get("action_group") and "operation" in result["inputSchema"].get("properties", {}):
        result["inputSchema"]["properties"]["operation"]["enum"] = sorted(actions or [])
    return result
