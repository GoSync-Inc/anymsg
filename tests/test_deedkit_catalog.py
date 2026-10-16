"""The deedkit npm client must list exactly the reviewed gateway catalog (no network, no database)."""
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]


def gateway_tools():
    catalog = json.loads((root/'services/gateway/src/agent_gateway/catalog.json').read_text())
    tools = {tool['name']: tool['annotations'].get('readOnlyHint') is True for tool in catalog}
    # Added by services/gateway/src/agent_gateway/catalog.py, never listed in catalog.json.
    tools['platform_capabilities'] = True
    return tools


def test_deedkit_client_tools_equal_gateway_catalog():
    rows = json.loads((root/'packages/deedkit/lib/tools.json').read_text())
    client = {row['name']: row['read_only'] for row in rows}
    assert len(client) == len(rows), 'duplicate tool in packages/deedkit/lib/tools.json'
    assert client == gateway_tools(), 'update packages/deedkit/lib/tools.json and index.d.ts for the gateway catalog'


def test_deedkit_type_declarations_list_every_tool():
    declaration = (root/'packages/deedkit/lib/index.d.ts').read_text()
    union = declaration[declaration.index('export type ToolName ='):].split(';', 1)[0]
    assert set(re.findall(r"'([a-z_]+)'", union)) == set(gateway_tools())
