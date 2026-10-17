# Media storage admission

## Managed original retention (schema 33/34)

The separate lifecycle worker can expire **processed, unpinned originals after
30 days without an original request**. Search and reading retained text do not
refresh that clock. An authorized original request does. Existing originals get
a full 30-day grace period when residency tracking is first installed; message
age alone never authorizes immediate deletion.

Residency belongs to shared SHA-256 bytes, while descriptions and source links
belong to individual occurrences. An operator pin protects all occurrences of
the same bytes. Eviction retains messages, filenames, titles, summaries,
extracted detail, ASR text and timestamp segments. Every active occurrence must
have usable current processing evidence. Unprocessed, stale, failed or
incompletely extracted/transcribed files are retained. A video description is
explicitly based on sampled frames and does not claim a complete transcript.

An operator who accepts losing originals without retained descriptions can add
`--expire-unprocessed` explicitly. This allows the same journaled eviction without
requiring extraction, ASR or descriptions. Every active occurrence of the shared
bytes must also have a message older than 30 days. Pins, the 30-day original-access
clock, active requests/grants/processing, backup fences, integrity checks and pass
limits still apply. Existing messages and any derived text remain unchanged;
missing descriptions are reported honestly. The original can be requested through
the existing bounded provider queue; provider availability is not guaranteed.
Without this flag, usable complete current processing remains required.

Extraction and original-dependent enrichment pause while a matching managed
eviction journal is prepared/completed. They preserve processing attempts,
budgets and existing text until verified re-publication makes the original
resident again. Unknown missing or corrupt originals keep their normal failure
diagnostics. Install the updated indexer/enrichment SELECT grants with the release;
these roles gain no residency or journal mutation permission.

The flag does not shorten the migration's initial access grace period. If the
owner explicitly chooses immediate expiry of historical originals, adopt their
historical age as a separate one-time operation during the initial maintenance
window: retain the fresh compatible restore proof, record the exact residency
preimage and latest shared-source timestamps, quiesce original readers and
publishers, and update only unchanged unpinned resident entries under the
lifecycle/source locks. Reject active grants, requests, processing and backup
fences. Never backdate a real original access or a new publication. Preserve
this audit on the host, then inspect the bounded preview before applying. Do
not edit migration 033 or delete directly from the original directory.

Destructive retention is opt-in and defaults to a dry run. Each pass has file
and byte limits. It rechecks pins, recent access, active downloads/grants,
processing and durable backup fences, verifies the exact regular file's size
and SHA-256, and journals the transition before unlinking. A failed or
interrupted transition is reconciled by the lifecycle worker; it never scans
and deletes arbitrary files, Docker volumes or other projects' data.

Only a completed managed eviction allows authorized file search and retained
text/segment reads while the original is absent. Unexpected missing bytes
retain their existing unavailable/corruption behavior. Metadata identifies
`original_evicted` separately and suggests `request_file_download`. Recovery
uses the existing bounded collector queue and checks the retained digest. It
does not guarantee that Telegram or WhatsApp can supply an old original.
Failed/exhausted requests, ACLs, provider cooldowns and storage limits still
apply. Accepted best-effort recovery does not imply a cold archive exists.

Backup fences are durable, without automatic expiry, from capture through
confirmed delivery. Pending captures from older runtimes must be reconciled
before enabling retention; the new worker cannot assume they are fenced.
Do not clear a fence while any retry still references its originals.
See [backup streaming](BACKUP_STREAMING.md) for replacement verification and
[the backlog](BACKLOG.md) for source versus live status.

### Activation and operator controls

Apply schemas 033/034 and their role grants through a reviewed release from the
exact verified main revision, after a fresh compatible backup has been restored
independently. Keep migrations 001..032 unchanged. Reconcile all older pending
backup jobs and snapshots before enabling retention; do not release a durable
fence merely because it is old. The receiver must retain a verified protected
anchor while its replacement is captured, delivered, decrypted and restored.

Install `file_lifecycle_role.sql` as the trusted migration owner and grant its
NOLOGIN role to a dedicated runtime login. Store that login's DSN in the private
`file-lifecycle.dsn` file. The runtime cannot edit source identities or remove
backup fences. `deploy/compose.file-lifecycle.yaml` is an optional overlay; its
profile starts with dry-run reports and bounded daily passes. Inspect the bounded
preview and blocked-reason report before explicitly adding `--apply` to the
reviewed runtime command. Repeated previews inspect the same first batch; they
do not constitute a full catalog audit. Dry runs never update the access clock
or traversal cursor. Apply passes advance a separate inspection cursor so blocked
old files cannot starve eligible files later in the catalog.

For an individual CAS digest, use the same restricted operator runtime:

```sh
python -m messaging_service.file_lifecycle --dsn-file /run/secrets/database_url --files /data/files --digest SHA256 --pin
python -m messaging_service.file_lifecycle --dsn-file /run/secrets/database_url --files /data/files --digest SHA256 --unpin
```

Install the independent understanding role/configuration separately, following
[ENRICHMENT.md](ENRICHMENT.md). The optional
`deploy/compose.file-understanding.yaml` overlay does not start a model daemon or
download weights. A local endpoint must be reachable in the worker's permitted
network namespace; container loopback refers to that container. Text/vision
profiles and their processing limits require explicit configuration. Existing
ASR results remain retained when model profiles change. Originals without usable
current processing evidence continue to occupy disk; enabling TTL alone cannot
reclaim a backlog of unprocessed videos under the default policy. The explicit
`--expire-unprocessed` policy can reclaim old originals without that evidence.

### Optional object storage

Object storage can be cheaper for originals than another fixed-size block
volume, but it adds a second copy policy, encryption keys, upload verification,
rehydration and access controls. It is not enabled by the retention worker.
As checked on 2026-10-16, Cloudflare R2 Standard lists $0.015/GB-month with a
10 GB account-level free storage allowance; 34 GB would be about $0.36/month
for storage if that allowance is available. Requests and taxes are separate.
R2 Infrequent Access lists $0.01/GB-month with retrieval charges and a 30-day
minimum, so its lower storage rate alone does not establish a lower total cost.
See [R2 pricing](https://developers.cloudflare.com/r2/pricing/).
No new bucket, storage subscription or upload is part of this implementation.

## Download admission and prefetch

Telegram and WhatsApp collector configurations use `media_prefetch_days: 7` by
default: automatically download originals from the rolling last seven days.
Set an integer from 1 to 36500 for another window, `0` for explicit requests
only, or `null` to prefetch all available history. This changes original-file
selection only; it never truncates captured messages, deletes stored originals,
or changes Telegram's separately configured `history_since` hard boundary.
Direct embedded `Archive.claim_media` calls retain their legacy unbounded
default; live collector entry points always pass the validated configuration.

An unexpired explicit original request is selected ahead of automatic prefetch,
including outside its rolling window. It does not override history bounds,
collection/account/chat access, deleted or superseded revisions, the attempt cap,
provider cooldown, file-size limits or storage admission. Outside-window jobs
remain queued without consuming a provider attempt. Requests do not reset failed
or exhausted jobs; originals may no longer be available from the provider.

Such dormant jobs cost an idle collector nothing per poll. With an integer
window, a claim reads one fresh database time after its lock waits and looks
only at candidates that can be claimable then: unexpired requests through the
`media_requested` index, and messages from the window (also bounded by
`history_since` and the five-minute future limit) through `message_scope_date`,
then their jobs through `media_message`. Its work grows with requests and
recent messages, never with the dormant backlog. `null` keeps the full history
scan by design. The retry-exhausted sweep, which marks an expired fifth lease
`failed`, has no selective index: each collector runs it on its first claim for
an account and then on the first claim at least 60 seconds after the previous
sweep, so the stored row lags by at most 60 seconds plus one claim cycle (about
one second on an idle collector). Attachment status and media requests report
such a lease as `retry_exhausted`/`failed` meanwhile; the operator status media
phase still counts it as a lease until the sweep. Expired storage reservations
are still reclaimed on every claim. `tests/test_media_claim_performance.py`
checks the plans with 30,000 and 60,000 dormant jobs.

Message capture and original downloads have separate disk budgets. Collector
`disk_reserve_bytes` retains the capture/spool floor (default 1 GiB).
`media_disk_reserve_bytes` controls the original-file floor (default the larger
of 10 GiB and the existing capture floor),
must be at least the capture floor, and can be configured independently. A
storage pause never changes message coverage into a successful history result.

Telegram and WhatsApp can opt into a separate floor for recent small originals with
`media_live_disk_reserve_bytes`. It is disabled by default (`null`), must remain
at least the capture floor, and cannot exceed the background media floor.
`media_live_max_bytes` defaults to the smaller of 32 MiB and `max_media_bytes`;
`media_live_window_seconds` defaults to 24 hours (allowed: 10 minutes to 7 days).
Only known-size originals within both limits qualify, based on message time;
unknown-size, older and larger originals retain the background budget. Future
timestamps more than five minutes ahead cannot enter this tier. History bounds
and account/chat exclusions apply to both tiers.

After explicit requests, eligible recent originals are claimed before background
history, newest first. An older or larger explicit request still uses the
background floor; requesting a file never silently lowers its storage budget.
Both tiers share the same CAS, atomic reservations and lease fences. The chosen
floor also applies during streaming, rather than only before download. This
policy keeps small fresh originals available during a background storage pause;
it does not implement cold-file eviction, guarantee latency during provider
rate limits or interrupt a transfer already in progress. Under sustained recent
traffic, history can wait. Extraction/transcription remains a separate queue:
agents can obtain a downloaded original before its analysis is ready.

Before starting a provider download, live collectors pass their `BlobWriter` to
`Archive.claim_media`. One PostgreSQL advisory transaction serializes all media
admissions in the archive DB. Filesystem device keys group aliases/bind mounts;
workers for one shared volume must use the same device identity in their runtime.
The lease reserves the declared original size plus 1 MiB streaming workspace.
Unknown sizes reserve the full configured `max_media_bytes` quota. Known files
larger than this limit fail before any provider request. Current Telegram and
WhatsApp paths stream directly into CAS; they do not stage another full-size
provider file. A future adapter that stages complete temporary originals must
budget its additional workspace before admission.

The calculation subtracts **full active reservations**, even if their writers
have already consumed some disk space. It is conservative, not a promise of
exact available capacity. It cannot protect against unrelated software filling
the same disk: the writer rechecks the media floor on every chunk and cleans up
its own pending file after interruption. No original is deleted or evicted by
this feature. A writer killed outright (SIGKILL, power loss) leaves a
`.pending-*` file; collectors remove such top-level files untouched for a day
at startup. If a stored original fails verification, the next verified download
of the same digest replaces it instead of failing with `blob_corrupt`.

Telegram original transfers get a deadline sized for paced 512 KiB requests
(at least one hour, at most six), so files above about 1.7 GiB can finish.

If there is insufficient capacity, the job stays `queued` with
`error_code=paused_storage`, retries after 60 seconds, and does not increment
provider attempts. A transfer interrupted by the local disk reserve returns to
the same state and rolls back its claimed attempt. Per-account/per-chat sync
diagnostics expose the reason; account media operations also expose
`storage_paused_jobs`. Terminal failures and queued retries have independent
counts and state-specific diagnostics: a terminal network failure never promises
another automatic retry. `needs_attention` remains true when other jobs are
simultaneously paused for storage. Other smaller eligible jobs can proceed on later passes.
Normal capture and metadata/text search remain independent of this pause.

A local write failure that is not a capacity limit (missing directory,
permissions, read-only mount, or the native adapter's temporary media directory)
is `error_code=storage_unavailable`, not `network_error`. Like a capacity pause it
does not consume a provider attempt and never fails the job terminally; it
retries every 5 minutes, and account media operations report
`state=storage_unavailable` and `storage_unavailable_jobs`. Fix the storage path,
then downloads resume on their own. Jobs recorded as `network_error` before this
classification keep their recorded code.

Media reservation expiry follows the media lease and is renewed with it.
Expiry, edit/delete supersession, successful completion and failure release
capacity. Cleanup is idempotent and matches the exact lease epoch, so an old
worker cannot release a replacement worker's reservation. The streaming guard
rejects writing after local lease expiry or beyond the admitted payload quota.
Current message deletion and current collection include/exclude policy are
checked before a job is claimed and when its lease is used.

Migration 21 adds `media_reservations`. Operator migration, collector DML grants
(`SELECT, INSERT, UPDATE, DELETE`) and backup table inventory must be updated
together. These transient capacity leases are not a durable blob residency or
cold-storage catalog. Trusted offline replay may use `claim_media` without a
writer for compatibility; live collectors always provide one. Gateway/MCP has
no direct media claim capability.

Migration 31 adds bounded download demand. Call `get_attachment(attachment_id)`
to inspect an existing occurrence without changing queues. If it is queued,
`request_file_download(attachment_id)` (gateway: `messages_request_file_download`)
requests priority for one hour. The caller needs the existing `download` right;
it cannot supply a URL, server path or provider descriptor. Duplicate requests
coalesce without extending that hour. Outstanding demand is capped at 32 per
credential and 128 globally; `media_request_limit` means to let earlier requests
finish. Poll `get_attachment` after the returned interval; requesting priority
does not promise completion or bypass any collection, retry or storage limit.
If a provider cooldown outlasts the one-hour request window, the response is
`deferred` with `next_due`/`retry_after_seconds`; no demand lease is created.
Request again after that time instead of repeatedly polling or resetting backoff.

Pending responses retain the full declared name, MIME, size, source message and
download reason. Download, text extraction and enrichment have independent
statuses. `get_attachment` verifies local presence before issuing a temporary
link; an absent registered original returns `original_missing`, without resetting
the download receipt or discarding the recorded processing statuses.

The seven-day prefetch window is **not** an eviction TTL. Original expiry is the
separate opt-in 30-idle-day policy described above. It preserves readable
text/analysis and requires the documented processing and safety checks; it does
not require a cold copy under the owner's accepted best-effort recovery policy.
Provider history/CDN availability alone is not a durable backup.

Sync status (`get_sync_status`, chat `resolve_reference.sync`, file search evidence)
counts each current file occurrence of a non-deleted message in exactly one
`download` state, using the same rule as each attachment's `download_status`:
a registered original is `ready` even if an edit re-queued its job, a legacy
original without a job is `ready`, and a `ready` job without a registered original
is `unavailable`. `download.ready` therefore equals `stored_files`.
`current_files` sums every state except `superseded` (replaced older revisions);
`download_basis: current_file_occurrences` names this rule. Queued originals
outside the prefetch window are known occurrences that are not stored yet.
