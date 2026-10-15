import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("preservation", ROOT / "scripts/verify_tiden_preservation.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def baseline():
    return {"product_id": "product", "workspace_id": "workspace", "branch": "main", "inventory": {
        "tests": [{"id": "test", "parentId": "suite", "signature": "a::b"}],
        "requirements": [{"id": "req", "sources": [{"id": "anchor", "repoPath": "a.py"}]}],
        "links": [{"id": "link", "testId": "test", "requirementId": "req"}],
        "components": [{"id": "component"}], "products": [{"id": "product", "telemetry": "binding"}],
        "branches": [{"id": "branch", "name": "main"}],
    }}


def test_equal_counts_do_not_hide_replaced_ids():
    a, b = baseline(), baseline()
    b["inventory"]["tests"][0]["id"] = "replacement"
    assert module.compare(a, b, {})


def test_metadata_and_nested_anchors_must_survive():
    for group, field in [("tests", "parentId"), ("requirements", "sources"), ("products", "telemetry")]:
        a, b = baseline(), baseline()
        del b["inventory"][group][0][field]
        assert module.compare(a, b, {})


def test_link_loss_fails():
    a, b = baseline(), baseline()
    b["inventory"]["links"] = []
    assert module.compare(a, b, {})


def test_only_reviewed_additions_are_accepted():
    a, b = baseline(), baseline()
    b["inventory"]["components"].append({"id": "new"})
    assert module.compare(a, b, {})
    assert not module.compare(a, b, {"additions": {"components": ["new"]}})


def test_changed_metadata_requires_exact_before_after_hash():
    a, b = baseline(), baseline()
    b["inventory"]["tests"][0]["signature"] = "changed"
    assert module.compare(a, b, {"changes": {"tests": ["test"]}})


def test_exact_reviewed_change_does_not_authorize_another_change():
    a, b = baseline(), baseline()
    b["inventory"]["tests"][0]["signature"] = "reviewed"
    allowed = {"changes": {"tests": {"test": {
        "before_sha256": module.digest(a["inventory"]["tests"][0]),
        "after_sha256": module.digest(b["inventory"]["tests"][0]),
    }}}}
    assert not module.compare(a, b, allowed)
    b["inventory"]["tests"][0]["signature"] = "unreviewed"
    assert module.compare(a, b, allowed)
