# ADR 001: a narrow direct CalDAV transport for the pilot

Accepted for stage 1: Python + httpx for CalDAV, icalendar for preserving/changing ICS, python-dateutil for recurrence expansion and defusedxml for XML.

The plan considered python-caldav as the first candidate. The pilot chose a small direct client in order to see conditional headers, redirects, the absence of automatic mutation retry and the raw server read-back explicitly. This is not a claim that python-caldav lacks such capabilities: its [documentation of conditional save/delete](https://caldav.readthedocs.io/latest/caldav/calendarobjectresource.html) describes `if_match` and support for version checks. If live compatibility requires another implementation, it must be verified with the same contract/fault set.

The advantage of the decision is a transparent, bounded flow of discovery/GET/REPORT/PUT/DELETE. The cost is own responsibility for CalDAV interoperability. ACL administration, scheduling, incremental sync, arbitrary server origins and a full production connector are not implemented now.

The Google integration remains a separate direct Google Calendar API adapter. The shared family model and the unified MCP will sit on top of the adapters. The Google Workspace CLI/MCP is not a dependency of this pilot.

Key protocols: [CalDAV RFC 4791](https://datatracker.ietf.org/doc/html/rfc4791), [iCalendar RFC 5545](https://datatracker.ietf.org/doc/html/rfc5545), [HTTP conditional requests](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1).
