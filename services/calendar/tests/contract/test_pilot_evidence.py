import asyncio
import json

import httpx

from family_calendar.pilot.evidence import validate_evidence
from family_calendar.pilot.icloud import Pilot
from family_calendar.pilot.journal import Journal
from family_calendar.private_files import write_private
from family_calendar.providers.caldav.adapter import CalDAVAdapter
from family_calendar.providers.caldav.transport import DAVTransport
from tests.fakes import MemoryDAV


def test_evidence_checker_rejects_mock_acceptance_and_tampered_readback(tmp_path):
    async def exercise():
        t = DAVTransport("a", "secret", client=httpx.AsyncClient(transport=httpx.MockTransport(MemoryDAV())))
        j = Journal(tmp_path / "run", "a", "cal1")
        try:
            pilot = Pilot(CalDAVAdapter("a", t), j, "https://caldav.icloud.com/a/c/", environment="mock")
            report = await pilot.run()
            result = validate_evidence(j.root)
            assert result["valid"] is True
            assert result["accepted"] is False
            assert report["scope"] == {"connection_id": "a", "calendar_ref": "cal1"}
            assert report["runtime_versions"]["icalendar"]
            assert report["checks"]["single_crud"]["step_ids"]
            report["gate_a"] = "accepted_core_and_future"
            write_private(j.root / "report.json", json.dumps(report).encode())
            result = validate_evidence(j.root)
            assert result["valid"] is False
            assert "invalid_acceptance" in result["issues"]
            report["gate_a"] = "not_accepted"
            write_private(j.root / "report.json", json.dumps(report).encode())
            with j.db:
                j.db.execute(
                    "UPDATE steps SET observed_data=? WHERE id=(SELECT MIN(id) FROM steps)", (b"tampered",)
                )
            result = validate_evidence(j.root)
            assert result["valid"] is False
            assert any(x.startswith("invalid_readback:") for x in result["issues"])
        finally:
            j.close()
            await t.close()

    asyncio.run(exercise())
