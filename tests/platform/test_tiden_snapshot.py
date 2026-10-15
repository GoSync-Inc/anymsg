"""The exporter must reject incomplete or moving read-only inventories."""
import importlib.util
from pathlib import Path

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
