# File understanding, idle originals and verified backup replacement Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Keep one verified off-site recovery point; retain readable file evidence while allowing unpinned, processed originals to expire after 30 idle days.

**Architecture:** Extend the existing content-addressed archive and bounded processing workers. Track residency separately from attachment occurrences and derived content. Persist an eviction journal and fail closed behind durable backup fences. Store titles and summaries independently of ASR and extraction, so summarizing cannot overwrite transcripts. Only explicitly managed eviction permits reading derivatives without an original. Provider recovery remains bounded and best effort. A replacement backup becomes the protected anchor only after independent verification; upload success alone cannot authorize deletion.

**Tech Stack:** Python 3.13, PostgreSQL 18, existing isolated enrichment providers, ffmpeg for bounded video sampling, pytest with synthetic fixtures, Tiden intent evidence.

## Decisions and integration contract

- User selected 30 days since last original request; initial observation starts a conservative 30-day grace period. Pins protect shared bytes across all occurrences. Unprocessed originals stay resident.
- Text, ASR segments, summaries, coverage and source references survive eviction. Unsupported, partial and failed processing remain explicit. A video description identifies sampled coverage; it cannot claim full transcription.
- Source schema 33 adds residency and durable backup fences; schema 34 adds independent understanding. Existing migration files remain unchanged. Store migration registration is updated by the lifecycle task.
- New `file_lifecycle.py` exposes shared transaction lock, durable backup fencing, `touch_original`, `pin_original`, `is_managed_evicted`, and an operator worker with dry-run default and bounded apply. Residency is keyed by SHA-256; every mutation validates registered bytes and active source occurrences. Each actual unlink is journaled and hash/size checked, and all eligibility is rechecked while fenced.
- New `file_understanding.py` owns `attachment_understanding`, keyed by occurrence, bound to current digest/revision and processing profile. It exposes current title, summary, coverage, state and provenance without changing `attachment_enrichment` transcripts. It reuses extraction/ASR and isolated inference; model profiles are opt-in, with account and daily limits, resource admission and no automatic cloud activation/model download.
- Parent integration owns file search/read SQL, attachment metadata, snapshot inventory and crypto support, deployment documentation and the backlog. Helpers must agree exact table columns/functions before parent integration. No helper performs live mutations.
- Original download requests only requeue a successful receipt when a matching managed eviction journal proves why bytes are absent. Failed/exhausted jobs and arbitrary missing files retain existing behavior. Rehydration must match the retained digest.
- Backup fencing is durable from capture through verified transfer completion, including crashes/retries; eviction fails closed while a fence exists. Restore inventories explicitly distinguish managed absent originals from corruption.
- Cold storage is evaluated only. No new disk, bucket, paid model or provider session is activated by this source change.

## Task 1: One verified off-site backup (B1)

Owner: receiver helper. Files: `services/messaging/messaging_service/backup_receiver.py`, new `tests/test_backup_anchor_rotation.py`, receiver section of `BACKUP_STREAMING.md`.

1. Write failing synthetic tests for independently verified anchor rotation, stale/mismatched proof, corrupt new or old bytes, missing proof, root/private path checks, retry idempotency, atomic replacement and preserving the last verified point on any failure.
2. Run the focused tests and observe expected failure.
3. Add a root-only local rotation operation under the existing receiver lock. It validates explicit independent restore evidence, expected previous marker identity, candidate ciphertext and committed receipt, atomically fsyncs the new marker, then prunes to one verified point. The forced upload key gains no new authority. Document that one retained point temporarily needs both copies while replacement is being verified; periodic pruning must not delete a pending verification candidate.
4. Re-run receiver/streaming/backup-operation tests. Obtain spec review, then quality review, and fix every material issue.

## Task 2: Safe 30-day residency (F1)

Owner: lifecycle helper. Files: migration `033_file_lifecycle.sql`, new `file_lifecycle.py`, `media_requests.py`, `archive.py`, migration registration in `store.py`, new `tests/test_file_lifecycle.py`, role SQL and operator entry point if needed.

1. Write failing synthetic PostgreSQL tests for idle expiry, pin/recent protection across duplicate occurrences, unprocessed/stale/partial derivatives, active grants/downloads/processing, backup fences, unlink failure/restart, dry run and bounded bytes/jobs.
2. Add persisted residency, conservative initial timestamps and pins; durable backup fences; replayable evicting/evicted states. No broad filesystem scan deletion. Verify SHA-256 before unlink and retain occurrence records.
3. Add explicit request handling for managed originals, bounded recovery through the existing collector queue, and digest validation when stored bytes return. Never reset a failed or exhausted attempt history merely because an original is missing.
4. Run lifecycle/download/storage tests on an owned disposable PostgreSQL instance. Obtain spec review then quality review.

## Task 3: Searchable file understanding (P1)

Owner: understanding helper. Files: migration `034_file_understanding.sql`, new `file_understanding.py` and bounded video helper, new `tests/test_file_understanding.py`, dedicated role SQL, `ENRICHMENT.md` additions.

1. Write failing synthetic tests for ASR-preserving summaries, PDF/Office/tabular text reuse, current source/profile publication, coverage/truncation, video sample descriptions, unsupported media, invalid input, budgets, lease expiry and resource exclusion.
2. Implement independent title/summary artifacts with explicit provenance and bounded background claims. Reuse existing local ASR, extraction and inference machinery. Local text and vision profiles are configured explicitly; lack of a model reports not configured. Video sampling is bounded in input bytes, duration, frames, pixels, time, output and subprocess lifetime, with local-file-only protocols and no embedded external resources.
3. Never replace detailed transcripts with a summary, imply complete reading of a truncated document, or infer the contents of unexamined parts of a video.
4. Run focused synthetic tests; obtain spec review then quality review.

## Task 4: API, backup and deployment integration

Owner: parent. Files: `file_index.py`, `attachment_metadata.py`, read paths in `store.py`, `backup.py`, `backup_worker.py`, `backup_crypto.py` as necessary; related synthetic tests; `MEDIA_STORAGE.md`, `FILE_INDEX.md`, deployment examples and README. Product delivery status stays in Tiden; `BACKLOG.md` only retains navigation and historical IDs.

1. Add failing tests proving current summaries are searchable under existing ACL, text/segments survive managed eviction, hidden/stale sources remain inaccessible and arbitrary missing originals retain existing errors.
2. Expose explicit original residency/availability, understanding coverage and recovery actions. Update original-access timestamps only on real original requests. Keep paging and occurrence provenance.
3. Add failing backup tests for schema 33/34 inventories, explicit managed-absent blobs, fail-closed unexpected missing bytes, durable fences through reference streaming retries and release after verified transfer.
4. Implement inventory/fence integration and opt-in least-privilege deployment controls; keep destructive retention off by default until verified promotion and inspection.
5. Update existing Tiden requirements with source versus runtime status and document cold-storage costs with dated primary references. Do not create another roadmap.

## Task 5: Verification and review hand-off

1. Each task observes failing tests before implementation and records actual focused runs. Never rename/move existing test IDs. Use a separate owned PostgreSQL instance per concurrent test runner (`messaging_probe_test`); no production data or sessions.
2. Resolve the four frozen promises and changed DK-03 decision in Tiden through candidates/settle; derive real falsifying cases and report actual JUnit results (`tiden run create`, `tiden run report --junit`, `tiden run complete`). Read progress and fix open items.
3. Run integrated relevant suites and CI-required checks; review all migrations, roles, wheel contents if packaging changes, crash paths and destructive operations. Final integration review follows completed spec and quality reviews.
4. Push `codex/file-lifecycle`, create a reviewable PR with the exact Tiden branch link, attach it, link the intent PR and close only after required evidence passes. Source merge and production activation remain distinct actions. No copied source patch is deployed.

## Operational verification outside synthetic tests

Receiver space and protected marker are measured on the separate receiver host. Never use general Docker prune. Investigate verification of the existing newer encrypted copy in quarantine before changing the protected anchor; never delete the only independently verified copy to create room. Keep private receipts, manifests, restored data and credentials outside Git. Any live step must follow the production runbook and existing authority.
