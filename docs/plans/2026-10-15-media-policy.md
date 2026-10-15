# Reliable original-file access Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Restore reliable recent and explicitly requested original access without coupling message-history depth to media prefetch or deleting unverified originals.

**Architecture:** Existing collectors remain the only owners of provider sessions. Durable per-occurrence demand leases prioritize bounded retrieval; shared disk reservations and streaming floors still apply. A seven-day media prefetch policy is independent from fixed history bounds. Automatic original eviction requires verified durable cold storage and a shared residency lifecycle; until configured, produce a read-only retention/capacity report and do not delete originals.

**Tech Stack:** Python 3.13, PostgreSQL 18, existing MCP/gateway, Telethon and whatsmeow.

## Delivery boundaries

P0 now: common fresh-media policy for Telegram and WhatsApp, explicit bounded download requests, usable pending/ready/missing metadata, exact live file verification. Existing approved sends are consumed and must not be repeated. No credentials, messenger source data or private host paths enter Git.

P1 next: explicit durable cold destination, blob residency/access/pins and archive-before-evict lifecycle, independent derived-text reads, then automatic byte/age eviction and rehydration. Merely deleting CAS today would hide derived text. Provider re-download is best effort and cannot substitute for a durable original.

P1 separately: persisted history setup and recent-first wave runner; current preview is not an executor.

P2: subscription ACK/outbox + authenticated webhook host wake, negotiated MCP notification hints; feedback submission API with private operator queue, idempotency, separate permission and bounded sanitized diagnostics. No automatic public GitHub issues.

## Task 1: common bounded media policy

Modify `services/messaging/messaging_service/archive.py`, `telegram_collector.py`, `whatsapp_collector.py`; create `media_policy.py`; examples and `MEDIA_STORAGE.md`. Add tests first in `tests/test_media_policy.py`. `media_prefetch_days` defaults to 7; null explicitly restores all available prefetch and 0 permits demand only. Do not alter captured history or old rows. Add WhatsApp parity for existing recent-small writer options. Demand requests expire in one hour and can never bypass account/chat policy, source revision, file limits, provider cooldown or disk floor. Archive.claim_media keeps legacy direct-call defaults compatible; collectors pass the configured policy.

## Task 2: request an existing original

Create migration `031_media_requests.sql` adding requested_until and request_key_digest to media_jobs (no new backup table). Create `media_requests.py`, integrate Store/app/SDK/gateway catalog with a download permission ceiling. Add actual PostgreSQL tests first. Request a visible current occurrence only. Coalesce duplicate requests without extending the first lease; cap outstanding demand per key and globally; do not reset exhausted/permanent provider attempts. Return source, phases, reason and next action. No network calls or waits in API transactions.

## Task 3: file status and presentation

Create `attachment_metadata.py` helper; improve pending descriptor metadata in result_evidence and conversation (no original readiness inference from MIME). Tests first in `tests/test_attachment_metadata.py`. Provide declared vs verified fields, complete filename, source IDs, retry/blocking state and instructions for requesting/polling originals. get_attachment stays passive; absent original never receives a ready download URL. Text extraction readiness remains separate. Do not weaken existing corruption/integrity checks to simulate cold support.

## Task 4: bounded capacity and retention planning

Measure physical CAS/dedup, queued known/unknown bytes, disk free and temporary/backup usage with bounded server-side reads. Inspect application-owned disposable build artifacts before considering original removal. Add an operator policy preview with separate text depth/media days/cache budget/floor and a concrete cold-storage prerequisite. Existing backup failures remain visible. No generic docker/volume prune or deletion of unrelated state.

## Task 5: verification and delivery

Run new regression tests RED then GREEN on isolated synthetic PostgreSQL, then relevant archive/storage/access/collector/API/gateway suites. Independent spec then code-quality review. Preserve migrations 1–30 and exact source IDs. Update CI/Tiden links without broad remapping of requirements. Deploy narrow changed services with preflight/rollback and read-only verification of existing authorized file; do not send messages. Record precise limits and future work, update shared state append-only and main only after checks.
