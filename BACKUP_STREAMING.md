# Bounded streaming backup (operator-only, opt-in)

`delivery_mode: "stream_v2"` avoids both a complete additional original tree
and a complete ciphertext file on the source VM. The default remains
`staged_v1`; existing format1/format2 encrypted bundles remain restorable.
This transport change has no MCP endpoint and no messenger authentication.

## Capture and delivery

The isolated worker takes the same exported PostgreSQL snapshot and cold
session/settings/spool capture as the existing backup. Collectors are resumed
after cold capture/dump, before original hashing and encryption. Reference-mode
capture stores the dump, recovery objects and manifest; its `files/` directory
is empty. It is an operator workspace, **not a completed/restorable backup**.
Normal `verify_bundle` rejects such a workspace; its explicit `blob_root` option
is used only by the operator streaming code.

Originals are read from the immutable, read-only CAS. Every referenced digest
and size is verified, then each source is hashed again while normalized USTAR
reads it. The manifest itself and database dump are also checked during reads.
No original is linked, copied, deleted or modified. Reference capture must not
be combined with future original eviction unless that implementation adds a
shared backup/blob lifecycle fence; until then, the current immutable CAS must
stay available for a retry.

Pinned binary `age` writes into a bounded pipe/FIFO. The host bridges this stream
to SSH; only the host has the forced-command delivery key. The container retains
its existing read-only mounts, public age recipient, private tmpfs PGPASSFILE and
restricted resource limits. There is no full source-side ciphertext spool.
Small plaintext dump/recovery metadata still require disk capacity and are kept
only until delivery finishes.

Receiver v2 first reserves a conservative **maximum** ciphertext size against
its per-object limit, quota and free-space reserve. It accepts frames of at most
1 MiB and checks actual size/free reserve throughout delivery. After FIFO EOF,
the host requires the independent encoder result (source integrity, successful
age exit, ciphertext length/hash) before sending the final frame. An interrupted
or corrupt source, failed encoder, oversized/truncated stream, bad footer or
receiver error cannot publish a backup. Receipt means ciphertext integrity;
it does not replace decryption and isolated restore verification.

The receiver commits a private manifest-digest binding before atomically linking
the complete encrypted object. A crash can leave an orphan binding or incomplete
temporary file; a retry discards only unpublished data. A committed ID is never
replaced. V2 accepts a retry with the same ID and manifest SHA256 and returns its
existing size/hash receipt without sending ciphertext back. This resolves loss
of the final receipt despite age encryption being randomized. A different
manifest under the same ID is rejected.

## Retry and crash recovery

`stream_upload` in the host journal keeps the exact original DB/recovery capture
when transmission fails. `--recover-stop` stops only the labelled owned worker,
resumes only saved previously running collector IDs, and retains the capture.
The next job re-encrypts that same capture without stopping collectors or taking
a different DB/session snapshot. Changed or absent immutable sources cause a
safe failure; they are never silently excluded. A durable final delivery receipt
allows completion of interrupted local cleanup without another live capture.

V1 still retains/retries the same staged ciphertext object. Its behavior and
manual `encrypt_snapshot` preservation are unchanged. A receiver upgrade supports
both versions; a sender switch requires the new matching worker and host runner.

## Operator rollout gate

1. Run `tests/test_backup*.py` with an isolated PostgreSQL test database. Run an
   actual pinned-age encrypt/decrypt and pg_dump/pg_restore drill with sessions,
   spool and settings fixtures in quarantine. Never send or activate restored
   collectors. Current supported backup schema range is 2..21.
2. Install the new root-owned `backup_receiver.py` on the existing backup host
   without changing the restricted key's shell/read/delete privileges. The forced
   command stays the same receiver invocation; it accepts v1/v2 from stdin and
   never executes `SSH_ORIGINAL_COMMAND`. The v2 caller's `backup-put-v2` string
   is an informational selector, not an executable server command.
3. Set receiver `--max-bytes` and `--quota-bytes` from the measured archive and
   conservative wire bound. Default 20 GiB/object and 30 GiB/total are **not**
   sufficient for a 29 GiB archive plus retained copies. Free/quota admission
   includes old copies and the complete new object before any retention runs.
   Preserve the existing independently verified restore anchor.
4. Build/pin the matching backup image and install the host runner. In the private
   existing runner JSON add `"delivery_mode": "stream_v2"`; mounts/recovery sources,
   SSH key, known_hosts and recipient stay operator-owned. Do not copy secrets
   into the image or examples. This mode is not activated by a library upgrade.
5. Deliver and independently restore one new backup before declaring the change
   operational. Verify manifest/tables/original hashes and cold recovery objects,
   then inspect source/receiver status, collector resume and staging disk peaks.
   Live restore/decryption limit must be raised explicitly to fit measured data.

## Retention

Use receiver-host maintenance `--prune --protected-file <root-owned-marker>
--max-copies 3` to keep at most three committed copies **including** every verified
anchor, filling remaining slots with the newest copies. If anchors exceed the
selected cap, or a protected ciphertext fails verification, deletion is refused.
The old daily/weekly presets remain available when `--max-copies` is omitted.

Size transfer deadlines from measured **end-to-end** throughput. A streamed
upload includes source integrity checks and encryption before/during delivery;
the receiver's alarm begins when the SSH command starts, even before its first
ciphertext frame. Fifteen minutes is insufficient for a 30 GiB archive at
20 MiB/s. Set explicit receiver `--timeout-seconds`, runner `timeout_seconds`
and systemd `TimeoutStartSec` together. Keep a finite whole-job deadline and
verify stop/retry preserves the committed capture without stopping collectors
again. A larger CPU cap alone may not improve a transport-limited upload.
Only root maintenance can prune; the upload key cannot choose this operation.
Associated v2 receipt bindings are removed with an authorized expired object.
Do not prune existing verified copies to make admission appear successful.

Examples/configuration are generic. Production activation, a real encrypted
delivery and isolated restore are separately recorded evidence, not implied by
unit transport stubs or this document.
