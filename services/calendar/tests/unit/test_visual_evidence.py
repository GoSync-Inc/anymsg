import asyncio
import json

import httpx
import pytest

from family_calendar.errors import CalendarError
from family_calendar.pilot.icloud import Pilot
from family_calendar.pilot.evidence import validate_evidence
from family_calendar.pilot.journal import Journal
from family_calendar.pilot.visual import agent_visual_evidence, visual_verified
from family_calendar.private_files import write_private
from family_calendar.providers.caldav.adapter import CalDAVAdapter
from family_calendar.providers.caldav.transport import DAVTransport
from tests.fakes import MemoryDAV


def test_agent_evidence_rejects_missing_tampered_and_outside_artifacts(tmp_path):
    write_private(tmp_path / "screen.png", b"\x89PNG\r\n\x1a\nexample")
    write_private(tmp_path / "state.txt", b"Observed synthetic events")
    visual = {
        "source": "agent_observed",
        "result": "pass",
        "future_result": "not-checked",
        "native_range_result": "not-checked",
    }
    assert not visual_verified(tmp_path, visual)
    checks = {
        name: {"result": "pass", "artifacts": ["screen.png", "state.txt"]}
        for name in (
            "single_crud",
            "conditional_write",
            "single_cancel_series",
            "properties_tz_dst",
            "all_day",
        )
    }
    write_private(tmp_path / "observations.json", json.dumps({"checks": checks}).encode())
    visual.update(agent_visual_evidence(tmp_path, tmp_path / "observations.json"))
    assert visual_verified(tmp_path, visual)
    write_private(tmp_path / "state.txt", b"Changed after recording")
    assert not visual_verified(tmp_path, visual)
    checks["single_crud"]["artifacts"] = ["../outside.txt", "screen.png"]
    write_private(tmp_path / "observations.json", json.dumps({"checks": checks}).encode())
    with pytest.raises(CalendarError):
        agent_visual_evidence(tmp_path, tmp_path / "observations.json")


def test_failed_agent_observation_is_recordable_without_acceptance(tmp_path):
    write_private(tmp_path / "screen.png", b"\x89PNG\r\n\x1a\nexample")
    write_private(tmp_path / "state.txt", b"Observed failure")
    checks = {
        name: {"result": "fail" if name == "all_day" else "pass", "artifacts": ["screen.png", "state.txt"]}
        for name in (
            "single_crud",
            "conditional_write",
            "single_cancel_series",
            "properties_tz_dst",
            "all_day",
        )
    }
    write_private(tmp_path / "observations.json", json.dumps({"checks": checks}).encode())
    visual = {
        "source": "agent_observed",
        "result": "fail",
        "future_result": "not-checked",
        "native_range_result": "not-checked",
    }
    visual.update(agent_visual_evidence(tmp_path, tmp_path / "observations.json"))
    assert visual_verified(tmp_path, visual)
    visual["result"] = "pass"
    assert not visual_verified(tmp_path, visual)


def test_agent_evidence_accepts_native_jpeg_screenshots(tmp_path):
    write_private(tmp_path / "screen.jpg", b"\xff\xd8\xffexample")
    write_private(tmp_path / "state.txt", b"Observed synthetic events")
    checks = {"all_day": {"result": "pass", "artifacts": ["screen.jpg", "state.txt"]}}
    write_private(tmp_path / "observations.json", json.dumps({"checks": checks}).encode())
    assert agent_visual_evidence(tmp_path, tmp_path / "observations.json")["checks"]
    write_private(tmp_path / "screen.jpg", b"Not an image")
    with pytest.raises(CalendarError):
        agent_visual_evidence(tmp_path, tmp_path / "observations.json")


def test_agent_visual_source_is_preserved_and_native_failure_stays_disabled(tmp_path):
    async def exercise():
        transport = DAVTransport(
            "a", "secret", client=httpx.AsyncClient(transport=httpx.MockTransport(MemoryDAV()))
        )
        journal = Journal(tmp_path / "run", "a", "cal1")
        try:
            # Tests gate logic with a mock transport; not a real iCloud attestation.
            pilot = Pilot(
                CalDAVAdapter("a", transport),
                journal,
                "https://caldav.icloud.com/a/c/",
                environment="icloud_live",
            )
            report = await pilot.run()
            visual = {
                "source": "agent_observed",
                "result": "pass",
                "future_result": "pass",
                "native_range_result": "fail",
            }
            write_private(journal.root / "visual-check.json", json.dumps(visual).encode())
            assert (await pilot.run())["gate_a"] == "not_accepted"
            write_private(journal.root / "screen.png", b"\x89PNG\r\n\x1a\nexample")
            write_private(journal.root / "state.txt", b"Observed synthetic events")
            checks = {
                name: {
                    "result": "fail" if name == "native_range_probe" else "pass",
                    "artifacts": ["screen.png", "state.txt"],
                }
                for name in report["checks"]
            }
            write_private(journal.root / "observations.json", json.dumps({"checks": checks}).encode())
            visual.update(agent_visual_evidence(journal.root, journal.root / "observations.json"))
            write_private(journal.root / "visual-check.json", json.dumps(visual).encode())
            report = await pilot.run()
            assert report["gate_a"] == "accepted_core_and_future"
            assert report["visual_evidence_source"] == "agent_observed"
            assert report["capabilities"]["native_range"]["status"] != "verified"
            assert validate_evidence(journal.root)["accepted"]
            write_private(journal.root / "screen.png", b"\x89PNG\r\n\x1a\ntampered")
            assert not validate_evidence(journal.root)["valid"]
            assert (await pilot.run())["gate_a"] == "not_accepted"
        finally:
            journal.close()
            await transport.close()

    asyncio.run(exercise())
