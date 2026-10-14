"""Messenger-operation asset completeness, without installing or starting providers."""
from pathlib import Path
import tomllib

from anymsg.assets import ASSET_NAMES, export_whatsapp_assets, source_directory


def test_operation_asset_is_allowlisted_exported_and_in_wheel_build_inputs(tmp_path):
    assert 'operations.mjs' in ASSET_NAMES
    source = Path(__file__).resolve().parents[1]
    settings = tomllib.loads((source/'pyproject.toml').read_text())
    force_include = settings['tool']['hatch']['build']['targets']['wheel']['force-include']
    assert force_include['whatsapp/operations.mjs'] == 'messaging_service/whatsapp_assets/operations.mjs'
    result = export_whatsapp_assets(tmp_path/'synthetic-node-sources')
    assert 'operations.mjs' in result['files']
    assert (tmp_path/'synthetic-node-sources'/'operations.mjs').read_bytes() == (source_directory()/'operations.mjs').read_bytes()
