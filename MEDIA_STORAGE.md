# Media storage admission

Message capture and original downloads have separate disk budgets. Collector
`disk_reserve_bytes` retains the capture/spool floor (default 1 GiB).
`media_disk_reserve_bytes` controls the original-file floor (default the larger
of 10 GiB and the existing capture floor),
must be at least the capture floor, and can be configured independently. A
storage pause never changes message coverage into a successful history result.

Telegram can opt into a separate floor for recent small originals with
`media_live_disk_reserve_bytes`. It is disabled by default (`null`), must remain
at least the capture floor, and cannot exceed the background media floor.
`media_live_max_bytes` defaults to the smaller of 32 MiB and `max_media_bytes`;
`media_live_window_seconds` defaults to 24 hours (allowed: 10 minutes to 7 days).
Only known-size originals within both limits qualify, based on message time;
unknown-size, older and larger originals retain the background budget. Future
timestamps more than five minutes ahead cannot enter this tier. History bounds
and account/chat exclusions apply to both tiers.

Eligible recent originals are claimed before background history, newest first.
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
this feature.

If there is insufficient capacity, the job stays `queued` with
`error_code=paused_storage`, retries after 60 seconds, and does not increment
provider attempts. A transfer interrupted by the local disk reserve returns to
the same state and rolls back its claimed attempt. Per-account/per-chat sync
diagnostics expose the reason; account media operations also expose
`storage_paused_jobs`. Other smaller eligible jobs can proceed on later passes.
Normal capture and metadata/text search remain independent of this pause.

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
