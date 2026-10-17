# Pilot contract v1

For service v0.2, [SERVICE.md](docs/SERVICE.md) applies: PostgreSQL, opaque refs, separate principals/grants, the official MCP and REST with a shared policy layer. Targets/credentials are not part of the agent API. The pilot harness below remains a separate contract; its future/series write is not included in MCP.

`family_calendar/contracts.py` fixes `EventRef(connection_id, calendar_id, resource_id, occurrence_key)`, `Scope=single|future|series` and `WriteReceipt`. A verified receipt requires an evidence hash; `absent` is used for a confirmed deletion. The journal stores a more detailed internal representation of a step, including the payload and the actual read-back, and is not a public agent API.

`providers/base.py` is the account-bound Protocol of the current pilot: `discover`, `read`, `read_window`, conditional `put` and `delete`. An adapter is created with a fixed connection ID and a separate transport. Credentials are not chosen through a global "current account".

`Journal.prepare` checks the mutation intent before saving: create/update/delete are allowed; update/delete require a strong ETag. The executor checks that the href belongs to the chosen collection and the local write grant before a new external step. The production opaque-ref API, sync and compile-change will extend this boundary later.

## Limits

| Value | Pilot v1 |
|---|---|
| Python | 3.13 |
| ICS/HTTP response | at most 1 MiB |
| Expansion | at most 10 000 instances |
| Objects created in one run | at most 16 |
| Timezone fixtures | Asia/Makassar, Europe/Berlin, UTC |
| VTIMEZONE fixture generation | 2020–2035 |
| Future | simple weekly, COUNT/UNTIL/infinite, preserve_absolute |
| Network | HTTPS iCloud hosts, certificate verification, no env proxies |
| Mutation retries | none; unknown → reconciliation |
| Invitations/sharing | not supported |
| Concurrency | one operator/process per run |

`read_window` returns `complete_window` only after a correct CalDAV multistatus with full resources. 401/403, malformed XML, missing calendar-data and the response limit are errors; not an empty calendar. `series_inventory_complete=false`: this is not a full sync and not proof of free time for the whole family.

## Journal and evidence

Step states: prepared → sending → verified / conflict / unknown / failed / needs_attention. The intent is saved by a transaction with synchronous=FULL before HTTP; the request ID and payload hash are checked on a repeat. Verified stores the real read-back bytes, ETag and semantic hash. Unknown/sending does not repeat the mutation but reconciles the same href.

An expected stale-version 412 differs from an arbitrary conflict. After a critical auth/permission/grant failure the matrix does not start the next mutations. Long settling windows are not guessed: uncertainty stays needs_attention and the operator can repeat reconciliation later.

Report v1 contains scope, run/environment, runtime_versions, checks → step_ids, compact operation receipts, capabilities, generated_at/evidence_as_of and Gate A. Until a real server+client PASS, capabilities stay unverified; mocks are not accepted. The checker script verifies the stored bytes and hashes locally; it does not replace live/client evidence and does not prevent deliberate forgery by an owner of all the files.

The list of public errors is given by fixed codes, for example `reauth_required`, `provider_forbidden`, `version_conflict`, `write_unknown`, `conditional_write_unavailable`, `write_not_enabled`, `unsupported_recurrence`, `invalid_local_time`, `ambiguous_local_time`, `floating_time_requires_timezone`, `run_scope_mismatch`. The texts of provider exceptions, credentials and HTTP bodies are not printed in CLI errors.

The v1 file formats have not been released for a production service. Migration of early experimental SQLite/config schemas is not performed automatically. PostgreSQL migrations and worker fencing belong to the next stage.
