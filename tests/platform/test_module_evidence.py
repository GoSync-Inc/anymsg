import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "deedkit_evidence", ROOT / "scripts/tiden_ci.py"
)
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_module_reports_add_identities_without_changing_existing_checks(tmp_path):
    metadata = {
        "sha": "a" * 40,
        "ci_url": "https://example.test/ci/1",
        "checks": {name: "success" for name in ci.CHECKS},
        "module_checks": {name: "success" for name in ci.MODULE_REPORTS},
    }
    for name, (filename, _, _) in ci.MODULE_REPORTS.items():
        (tmp_path / filename).write_text(
            '<testsuite><testcase name="same" classname="tests.test_api" file="tests/test_api.py"/></testsuite>'
        )
    (tmp_path / "calls.xml").write_text(
        '<testsuite><testcase name="same" classname="tests.test_api" file="tests/test_api.py"><failure/></testcase></testsuite>'
    )
    summary = ci.prepare(tmp_path, tmp_path, metadata)
    checks = json.loads((tmp_path / "ci-checks.json").read_text())
    assert {
        c["signature"] for c in checks if c["signature"].startswith("anymsg-ci::")
    } == {"anymsg-ci::" + n for n in ci.CHECKS}
    assert (
        next(c for c in checks if c["signature"] == "deedkit-ci::calls")["execution"][
            "status"
        ]
        == "failed"
    )
    assert summary["test_counts"] == {"passed": len(ci.MODULE_REPORTS) - 1, "failed": 1}
    gateway = next(ET.parse(tmp_path / "normalized-gateway.xml").iter("testcase"))
    calendar = next(ET.parse(tmp_path / "normalized-calendar.xml").iter("testcase"))
    assert gateway.get("file") == "services/gateway/tests/test_api.py"
    assert gateway.get("classname") != calendar.get("classname")


def test_partial_module_outcome_manifest_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="every platform module"):
        ci.prepare(
            tmp_path,
            tmp_path,
            {"sha": "a" * 40, "checks": {}, "module_checks": {"gateway": "success"}},
        )
