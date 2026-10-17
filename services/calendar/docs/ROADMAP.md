# Service delivery

The current queue and statuses are kept only in the **[shared BACKLOG](../../../BACKLOG.md)**
(DK-15–DK-19). This document keeps the history of the stages and the pilot's boundaries.

## Current MVP v0.2

Implemented: Google/iCloud connections, PostgreSQL, family/agent grants, roles, bindings, versioned duties, agenda/slots, durable workers/leases/locks, MCP/REST and the local owner CLI. Live CRUD of individual events has been verified on Google and iCloud test calendars. The original iCloud core/future pilot has separate private receipts and visual observations.

This is part of stages 2–6, not acceptance of all gates. The remaining implementation and separate
checks of multiple accounts/deployment/restore are listed in the shared backlog.
Source refresh is bounded polling; unknown is not counted as free time.
[Current contract](SERVICE.md).

## Stage 1: iCloud pilot

The local harness code is implemented; the matrix below is kept as the history of the stage. Live results are in the owner's private reports; normal tests use synthetic fixtures and a mock transport.

| Task | Implementation | What still to check live |
|---|---|---|
| 1.1 Bootstrap/contract | Package, lock, account-bound Protocol, intent/receipt validation, limits | No account needed |
| 1.2 Onboarding/discovery | Hidden input, encrypted local registry, TLS/redirect/privileges checks | One account, then separately a second and shared permissions |
| 1.3 Journal/CRUD | Stable refs, conditional headers, durable read-back, cleanup | CRUD and a real 412 |
| 1.4 Single/series/roundtrip | Exceptions, alarms, TZ, unknown props, DATE/floating | Server persistence and Apple client behavior |
| 1.5 Future/faults/gate | Native probe, split COUNT/UNTIL/infinite, saved plan, compensation/reconcile, checker | Future split and native separately; owner visual report |

Fault injection (timeouts, revocation, conflicts) is done on a mock; it is not passed off as failures of the real iCloud. The full production schema/provider API from the project is not copied into the pilot: its current boundaries are described in CONTRACTS.md. The file structure combines the plan's small modules, keeping the corresponding behavior.

## Boundaries of the delivered MVP

PostgreSQL, Google OAuth/API, several connections, basic family roles/grants,
agenda/slots, duties, durable workers and MCP/REST are already implemented. The list of
old stages 2–7 is not a list of capabilities not yet written. Server
deployment templates are part of the platform; evidence of a specific cutover
and restore is counted separately from their presence in the repository.

Future is enabled after its PASS. Several calendars are already available through the local MCP in an explicit permission scope; automatic moves of linked family activities and projections are not yet enabled.
