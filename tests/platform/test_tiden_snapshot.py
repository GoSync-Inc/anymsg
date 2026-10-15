"""The exporter must reject incomplete or moving read-only inventories."""
import importlib.util
import json
from pathlib import Path
import stat

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("snapshot", ROOT / "scripts/tiden_snapshot.py")
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


def page(items, cursor="", total=2, revision="r1"):
    return {"tests": items, "pagination": {"nextPageToken": cursor, "totalCount": total}, "revision": revision}


def test_multiple_pages_and_empty_intermediate_page():
    responses = {"": page([{"id": "a"}], "1"), "1": page([], "2"), "2": page([{"id": "b"}])}
    assert [x["id"] for x in snapshot.pages(lambda c: responses[c], "tests")] == ["a", "b"]


@pytest.mark.parametrize("responses", [
    {"": page([{"id": "a"}], "1"), "1": page([], "1")},
    {"": page([{"id": "a"}], total=2)},
    {"": page([{"id": "a"}], "1"), "1": page([{"id": "a"}])},
    {"": {"tests": []}},
    {"": page([{"title": "missing id"}], total=1)},
    {"": page([{"id": "a"}], "1"), "1": page([{"id": "b"}], revision="r2")},
])
def test_partial_or_malformed_inventory_is_rejected(responses):
    with pytest.raises(snapshot.SnapshotError):
        snapshot.pages(lambda c: responses[c], "tests")


def test_two_reads_must_agree():
    with pytest.raises(snapshot.SnapshotError, match="changed"):
        snapshot.stable({"tests": [{"id": "a"}]}, {"tests": [{"id": "b"}]})


def test_order_does_not_change_fingerprint():
    assert snapshot.fingerprint({"tests": [{"id": "b"}, {"id": "a"}]}) == snapshot.fingerprint({"tests": [{"id": "a"}, {"id": "b"}]})


def test_ordered_test_content_is_not_treated_as_a_set():
    first = {"tests": [{"id": "a", "steps": ["prepare", "execute"]}]}
    second = {"tests": [{"id": "a", "steps": ["execute", "prepare"]}]}
    with pytest.raises(snapshot.SnapshotError, match="changed"):
        snapshot.stable(first, second)


def test_private_output_is_exclusive_and_does_not_follow_symlinks(tmp_path):
    path = tmp_path / "baseline.json"
    snapshot.private_json(path, {"complete": True})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"complete": True}
    with pytest.raises(FileExistsError):
        snapshot.private_json(path, {"complete": False})
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        snapshot.private_json(link, {})
    assert json.loads(path.read_text()) == {"complete": True}


def inventory_cli(*, source_count=0, link_count=0):
    cli = snapshot.CLI("unused", "product", "workspace", "main")
    calls = []
    def call(*args):
        calls.append(args)
        values = {
            ("test", "list"): page([{"id": "t", "linkedRequirementCount": link_count}], total=1),
            ("spec", "list"): {"requirements": [{"id": "s", "sourceCount": source_count}],
                               "pagination": {"nextPageToken": "", "totalCount": 1}},
            ("component", "list"): {"components": [], "pagination": {"nextPageToken": "", "totalCount": 0}},
            ("product", "list"): {"products": [{"id": "product"}], "pagination": {"nextPageToken": "", "totalCount": 1}},
            ("branch", "list"): {"branches": [{"id": "b", "name": "main"}]},
            ("test", "links"): {},
        }
        return values[args[:2]]
    cli.call = call
    return cli, calls


def test_inventory_uses_only_reads_and_checks_even_zero_link_tests():
    cli, calls = inventory_cli()
    result = cli.inventory()
    assert result["links"] == [] and result["requirements"][0]["sourceCount"] == 0
    assert {args[:2] for args in calls} == {("test", "list"), ("spec", "list"),
        ("component", "list"), ("product", "list"), ("branch", "list"), ("test", "links")}
    assert ("test", "links", "t", "--branch", "main") in calls


@pytest.mark.parametrize("counts", [{"source_count": 1}, {"link_count": 1}])
def test_missing_source_or_link_readback_fails_complete_inventory(counts):
    cli, _ = inventory_cli(**counts)
    with pytest.raises(snapshot.SnapshotError, match="incomplete"):
        cli.inventory()
