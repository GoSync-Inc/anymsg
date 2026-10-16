# Private R2 backup destination (operator-only, opt-in)

This receiver sends the existing age-encrypted `stream_v2` transport to a
dedicated Cloudflare R2 Standard bucket. The source keeps its upload-only SSH
key; it receives no R2 credential or read/list/delete interface. R2 credentials
stay on the separate receiver. The age recovery key stays on the independent
restore machine and is never copied to either server.

The [filesystem receiver](BACKUP_STREAMING.md) remains available. Installing the
module does not change an SSH forced command, switch a producer, schedule
backups, perform independent restores or authorize production changes. Follow
[verified promotion](docs/operations/production.md) for an exact current main SHA
with successful required CI. Never install feature-branch source, a copied source
patch or an ad hoc application image overlay on a host.

## Account and bucket preparation

1. The owner signs into Cloudflare and activates R2 through **Storage & databases
   → R2 → Overview**, accepting the checkout and payment terms. This is
   usage-based billing; a domain transfer or paid website plan is not part of
   this setup. [Cloudflare activation instructions](https://developers.cloudflare.com/r2/get-started/).
2. Create one dedicated **Standard** bucket. Keep public access, the public
   development URL and custom domains disabled. Do not add CORS rules: this
   server-to-server backup route needs none. Record its jurisdiction and matching
   S3 endpoint privately.
3. In the R2 API token controls, create **Object Read & Write** access restricted
   to that bucket. Do not grant account administration or all-bucket access.
   Save its Access Key ID and Secret Access Key directly in a private receiver
   credential file. The secret is shown only once; do not put it in chat, shell
   arguments, Git, examples or CI artifacts. A jurisdictional bucket requires its
   corresponding endpoint, such as `https://<ACCOUNT_ID>.eu.r2.cloudflarestorage.com`
   for EU. [Cloudflare R2 authentication](https://developers.cloudflare.com/r2/api/tokens/).
4. Do not add lifecycle expiration or storage-class transition rules for committed
   backups or receipts. Cleanup of **incomplete multipart uploads** is allowed;
   it does not replace root-controlled verified-point rotation. Check that no
   existing broad bucket policy expires the protected object.

The bucket-scoped credential can access objects within that bucket. The
upload-only boundary is enforced by the receiver's restricted SSH account and
fixed command, not by giving the producer this credential. Root on the receiver
remains trusted. Keep receiver credentials out of producer recovery settings.

## Cost and capacity

Prices checked on 2026-10-16: Standard storage is **$0.015/GB-month**, with
10 GB-month free per account, shared with its other Standard use. The request
allowances are one million Class A and ten million Class B operations monthly;
excess requests are billed. Direct R2 egress is free. Storage billing averages
each day's peak, so temporary old-plus-new overlap counts even when brief.
For a generic 35 GB daily replacement, budget about 70 GB at the daily peak:
`(70 - 10) × $0.015 ≈ $0.90/month` for storage if the allowance is available.
Taxes, growth, request charges and other account use can increase the bill.
This is an estimate, not a spending cap. Infrequent Access has a 30-day minimum
and retrieval fees, making it a poor fit for daily replacement.
[Cloudflare pricing and billing units](https://developers.cloudflare.com/r2/pricing/).

One retained verified point still requires room in R2 for the previous point
and one candidate. Configure a conservative maximum wire size and a quota that
admits both. The receiver counts committed and unresolved physical objects,
allows at most two full objects, and refuses accumulation after an unresolved
attempt. A quota refusal never authorizes deletion of the only recovery point.
Application admission limits do not cap the Cloudflare account's bill or other
clients' writes.

## Receiver installation and private configuration

Use a reviewed wheel built from the exact CI-verified current main revision and
the [receiver-only SDK lock](services/messaging/requirements-backup-r2.lock) from
that same revision. Install into a dedicated Python 3.13 environment. Set
`REVIEWED_WHEEL` to the inspected wheel and `REVIEWED_SOURCE` to the verified
clean revision; check their provenance before these generic installation steps:

```sh
python3.13 -m venv /opt/deedkit-backup-r2/venv
/opt/deedkit-backup-r2/venv/bin/python -m pip install --no-deps "$REVIEWED_WHEEL"
/opt/deedkit-backup-r2/venv/bin/python -m pip install --require-hashes \
  -r "$REVIEWED_SOURCE/services/messaging/requirements-backup-r2.lock"
```

The wheel's `--no-deps` installation deliberately avoids installing messaging
provider dependencies on this receiver. The module needs its sibling
`messaging_service.backup_receiver` helpers; copying one Python file is not an
installation. The SDK is optional and is not added to ordinary application
dependencies or images. Importing the application does not resolve AWS
credentials or connect to R2.

The receiver has no need for a messaging DSN, provider session, original file
tree, Docker socket or age private key. A host Python installation and the
receiver's explicit SDK dependencies suffice; do not activate the application or
collectors to install it. Inspect wheel/sdist contents and test the installed
wheel outside the checkout before host enrollment.

Copy [the generic configuration](deploy/backup-r2.example.json) to a private
operator path and replace every deployment-specific value. The endpoint must be
the account's explicit HTTPS R2 S3 endpoint; the client uses region `auto`,
signature v4 and explicit credentials. It does not fall back to ambient AWS
profiles or instance metadata. The credential JSON contains only these keys:

```json
{
  "access_key_id": "REPLACE_WITH_BUCKET_SCOPED_ACCESS_KEY_ID",
  "secret_access_key": "REPLACE_WITH_SECRET_ACCESS_KEY"
}
```

This is a shape example, not a usable credential. Files must be private, regular,
single-link and owned by their process user or root; symlink paths are refused.
Use mode `0600` for files and `0700` for private state directories. The receive
process uses its own private config and credential file, owned by the restricted
upload user. Root maintenance uses separate **root-owned** config and credential
copies: root does not accept an upload-owned configuration or credential. The
operator config must select the same endpoint, bucket, prefix and shared upload
state directory; only its `credentials_file` path changes to the root-owned copy.
The private upload-state leaf may belong to the upload user. Keep its ancestor
directories, root control and proof paths root-owned without group/other write
access. Control and proof paths must be outside upload state. Shared temporary
directories are unsuitable. Do not weaken these checks to fit a deployment.

The example uses 40 GiB/object, 80 GiB/total, two objects and 64 MiB parts. These
are explicit example limits, not measured deployment settings. Size the receiver
deadline, producer `timeout_seconds` and scheduler deadline together from
end-to-end capture, encryption, hashing, upload and full cloud readback time.
A receive deadline begins before the first data frame. Preserve a finite timeout
and enough RAM for the bounded part buffer; the receiver does not stage a
complete ciphertext locally.
Do not mount R2 as a POSIX filesystem: upload durability uses the object API,
receipts and journal, not filesystem rename assumptions.

Configuration version is `v: 1`; unsupported/duplicate fields fail closed.
Optional defaults are 64 GiB `max_bytes`, 128 GiB `quota_bytes`, two
`max_objects`, 64 MiB `part_bytes` and 7200 `timeout_seconds`. `max_objects` must
be exactly two. Part sizes may be 5–64 MiB; the object bound must fit both the
10,000-part limit and the backend's maximum. Deadlines may be 60–21600 seconds.
Set explicit values appropriate to the measured deployment rather than relying
on defaults. This implementation accepts the default and EU R2 endpoints.

## SSH boundary and producer enrollment

Use a dedicated upload key with a root-owned SSH home and `authorized_keys`.
Pin the receiver host key through an already trusted administration route. The
fixed forced command invokes only `receive`, for example:

```text
restrict,from="SOURCE_ADDRESS",command="/opt/deedkit-backup-r2/venv/bin/python -m messaging_service.backup_r2_receiver --config /etc/deedkit-backup-r2/receive.json receive" ssh-ed25519 REPLACE_WITH_UPLOAD_PUBLIC_KEY
```

The addresses and key above are placeholders. Preserve restrictions against
shell, SFTP, forwarding and agent access. The receiver never executes or selects
maintenance from `SSH_ORIGINAL_COMMAND`; stdin cannot choose a control/proof path.
Only the configured module invocation is run. Inspect the actual key restrictions
and file ownership before changing the existing upload route.

The producer keeps its existing host/user/key/known-hosts transfer configuration
and uses `delivery_mode: "stream_v2"`; this backend does not accept `staged_v1`.
No R2 key or endpoint is added to the producer. Do not switch an in-flight job
without reconciling its journal and exact prior destination: retrying a stale
capture is not taking a fresh pre-migration snapshot.

Before enrollment, use a separate synthetic prefix and configuration to deliver
an age-encrypted synthetic fixture through the real forced SSH route, download
it, compare its complete SHA256/size and decrypt/restore it in quarantine.
Exercise duplicate-ID delivery without producing a second logical backup. Use
no real accounts, sessions or production database in this synthetic probe.
Remove only its exact owned test objects/state after inspection; never run a
bucket-wide delete. Keep its evidence separate from actual backup acceptance.

## Commit, download and independent restore

The receiver accepts the same bounded v2 framing as the existing runner. It
uploads bounded multipart parts, but completes the multipart object only after
validating age framing, input EOF, size/hash and the producer's final encoder
confirmation. R2 supports multipart uploads for large objects; parts except the
last must satisfy the service's minimum size.
[Cloudflare upload documentation](https://developers.cloudflare.com/r2/objects/upload-objects/).

Each attempt has a random immutable physical key under
`<prefix>/objects/<backup-id>-<token>.tar.age`. A logical receipt at
`<prefix>/receipts/<backup-id>.json` binds that key to ciphertext size/SHA256 and
snapshot manifest SHA256. Its conditional `If-None-Match: *` publication cannot
replace a committed ID. A matching logical retry returns the committed receipt;
a conflicting ID fails. Multipart ETags are never treated as full-file SHA256.
Atomic conditional publication is an R2 API contract;
negative reads do not establish that an earlier unknown write cannot still
finish. [Cloudflare S3 compatibility](https://developers.cloudflare.com/r2/api/s3/api/).

Download by logical ID into a **new** private output file:

```sh
/opt/deedkit-backup-r2/venv/bin/python -m messaging_service.backup_r2_receiver \
  --config /etc/deedkit-backup-r2/operator.json download \
  --id BACKUP_ID --output /PRIVATE_NEW_DIRECTORY/candidate.tar.age
```

Download verifies the entire ciphertext's size and SHA256 before atomic output
publication. If performed on the receiver, securely transfer that ciphertext to
the isolated restore machine and verify the complete hash there again. The
decryption key stays on that machine. Alternatively use separately authorized
private operator download configuration there. Never give the producer download
authority or assume that HEAD/ETag or upload success proves recoverability.

Follow the [backup restore drill](BACKUP.md#restore-drill) with an explicitly
sufficient decryption bound, matching PostgreSQL tools/runtime and enough private
disk. Verify authenticated decryption, bundle integrity, restored database/table
inventory, original hashes and cold recovery objects. Quarantine restored
collectors, sessions, keys and outbound work; do not start a provider client.
Record the actual schema, backup ID/size/hash/manifest hash, verification ID and
timezone-aware verification time in the private proof described in
[the independent proof contract](BACKUP_STREAMING.md#retention). All seven checks
must reflect the actual independent restore. An upload receipt, successful unit
test or browser subscription checkout is not this proof.

## First protected point and one-point rotation

Run maintenance locally as root with trusted configuration and credentials.
Keep the proof and protected marker in separate private root-controlled paths
outside uploader state. Their files must be regular, private and single-link;
every ancestor must be root-owned, nonsymlinked and not group/other writable.
Never accept an uploader-authored proof or marker. The R2 marker uses a
`v`/`destination`/`backups` envelope binding it to this configured destination;
do not copy the filesystem receiver's bare-list marker into it.

Initial enrollment requires an absent marker and an independently restored
committed cloud point. It deletes nothing:

```sh
/opt/deedkit-backup-r2/venv/bin/python -m messaging_service.backup_r2_receiver \
  --config /etc/deedkit-backup-r2/operator.json protect \
  --protected-file /etc/deedkit-backup-r2/control/protected.json \
  --restore-proof /etc/deedkit-backup-r2/proofs/initial-restore.json
```

If enrollment is interrupted, preserve its root-owned enrollment journal and
retry the same proof bytes. An existing marker is accepted only as a matching
journal-backed replay; do not remove or reseed it to force initial enrollment.

For each replacement, keep the previous cloud point while delivering and
independently restoring the candidate. Record the **exact existing marker
bytes' SHA256** privately before rotation. Then pass that digest, for example
using `PREVIOUS_MARKER_SHA256` supplied by the operator:

```sh
/opt/deedkit-backup-r2/venv/bin/python -m messaging_service.backup_r2_receiver \
  --config /etc/deedkit-backup-r2/operator.json rotate \
  --protected-file /etc/deedkit-backup-r2/control/protected.json \
  --restore-proof /etc/deedkit-backup-r2/proofs/replacement-restore.json \
  --expected-protected-sha256 "$PREVIOUS_MARKER_SHA256"
```

Rotation holds the shared receiver lock, checks destination, exact marker digest,
receipt/proof identities and object hashes, then fsyncs its root-owned pending
journal. It switches and fsyncs the marker **before** deleting only the exact
superseded ciphertext and logical receipt. It does not remove unrelated
candidates. Preserve the successful journal, proof and marker for audit/retry.
Serialize this procedure with new deliveries. Two occupied slots require
candidate reconciliation/rotation before another full upload, not higher limits
or automatic deletion to hide the failure.

Do not schedule `--prune --max-copies 1` on the old receiver or apply age/count
pruning to cloud objects. Disable the old scheduled count/daily/weekly prune
before enrollment of this policy. No unattended independent restore scheduler
is supplied by this receiver: automation must actually establish the same proof
before promotion and report failures. Upload completion alone never rotates.

## Unknown outcomes and migration from local archives

On an error or timeout, preserve private state, receipts, proof and journal.
Reconcile the same logical ID, attempt, snapshot and destination. A multipart
completion with an unknown result retains its exact attempt evidence and blocks
a new replacing attempt; a single negative HEAD is insufficient to discard it.
Conditional receipt publication can retry only its exact body. Do not erase a
journal, invent another backup ID or broadly abort/delete objects to get past an
uncertainty. Failed parts may be aborted only when their unpublished status and
ownership are established.

For an interrupted rotation, retry with the **same proof bytes and original
expected marker SHA256**. Keep the pending journal, including after the marker
has switched; the operator rechecks the new protected point before any remaining
exact deletion. Finish or explicitly reconcile that operation before another
rotation. A storage permission/timeout error is not evidence of absence.

When moving from local archives, retain both the old protected local anchor and
any existing candidate until the selected cloud copy has its complete ciphertext
hash verified and has been independently decrypted/restored from R2. Protect the
cloud point only after that proof. Preserve the old marker, proof and journals;
only then may a separate reviewed operation remove exact obsolete local archive
IDs and their receipts. Do not delete the sole anchor to fit the first transfer,
prune shared Docker data, touch another project's volumes, or treat a cloud copy
of an old snapshot as a fresh pre-migration backup.

Record these gates separately: exact-main source/CI and package checks; bucket
privacy and scoped credential enrollment; synthetic live-bucket transport drill;
actual fresh compatible snapshot delivery; independent quarantined restore;
protected-point enrollment/rotation; and approved application release. This
document and synthetic CI do not claim live acceptance for any deployment.
