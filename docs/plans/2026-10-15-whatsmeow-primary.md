# Primary WhatsApp provider: whatsmeow

Status: implementation and offline regression complete in an isolated branch. Prepared for the current DeedKit main after the repository migration; production account cutover remains separate.

## Target

Replace Chromium for new WhatsApp deployments with a pinned Go/whatsmeow process,
retaining the existing collector lease, policy, outbox, normalized archive IDs,
file pipeline and independent background/action IPC lanes. Keep webjs as explicit
rollback provider. No SQL checksum or Tiden ID changes.

## Scope and acceptance

1. Native provider implements start/dialogs/page/media/send_text/messenger_operation
   and pairing; durable capture precedes provider ACK. Existing c.us/g.us/lid and
   serialized message IDs remain stable, verified aliases only. Persist replayable
   provider evidence privately; bounded disk, history and file transfers.
2. Preserve message kinds, ordering uncertainty, formatting, author/owner attention,
   edits, revokes, reactions and receipts. History remains partial unless proven;
   requesting older history does not imply full coverage.
3. Map the complete current operation catalog to typed native APIs. Explicit
   unsupported operations remain denied; submitted mutations with missing receipt
   stay unknown, without automatic retries. Enforce fresh policy and expiration on
   both IPC lanes; never expose raw provider RPC or credentials.
4. Python selector/config/pairing and native Docker overlay use the existing runtime
   layout. New example configuration chooses whatsmeow. Existing browser sessions
   are not converted or silently logged out; explicit rollback remains possible.
5. Useful native additions in this slice: reliable global revoke without local-delete
   fallback, bounded history request support and durable provider retry evidence
   where supported. Broader newsletter/community features require separate scoped
   contracts and are recorded rather than exposed unsafely.
6. Synthetic contract, operation and replay/security tests; Go race/vet/build;
   targeted existing Python/Node regression. Live read-only verification may use
   the separately linked WA2 pilot on the VPS. Real sends/admin changes need exact
   owner approval. Cutover is conditional on parity and repository migration status;
   no false claim of full live parity or completed soak.

## Implementation ownership

Go in whatsapp/whatsmeow (portable root subtree, unchanged by migration).
Python in services/messaging/messaging_service, deployment overlay and docs are
integration-owner changes in this separate worktree. Migration agent files are
never edited in its worktree. Tests retain existing root tests identity.

Before commit: independent product/architecture/code critique, final diff/security
review, documented rollout/rollback and unresolved live checks.


## Implemented and verified (2026-10-15)

- Native durable capture/history/replay, saved-session identity checks, two IPC
  lanes, media/send/administration dispatch and provider-aware Python manifests.
- Explicit history request and safe history-batch diagnostics; global revoke
  without local-delete fallback. New setup defaults to native, legacy configs
  retain webjs. No existing SQL migration or Tiden requirement IDs changed.
- Independent review findings fixed: ACK/decryption crash window, bounded
  history downloads/retries, outgoing protocol retry evidence, group member
  failure semantics, expired pins, full-spool recovery, bounded replay and
  consumer/unlink races. See the acceptance matrix for evidence and limits.
- Final Go: 54 tests, race detector, vet and static Linux build passed. Independent
  reviewer reran the four final replay/quota/race regressions successfully.
- Python full regression: 2050 passed, 11 skipped before the final two diagnostics
  tests; the final diagnostics/collector-health selection passed 25 tests.
  Go-to-Python contract suite passed 25 tests. Node regression passed 144 tests
  with serial execution. Wheel/sdist/assets verification passed 8 tests.
- Final native Docker image built and passed the read-only, network-disabled
  smoke check as UID 10001 with no Node or Chromium installed.
- Read-only saved-session and exact fresh outgoing self-message checks passed on
  the separately linked VPS pilot. Its provider message ID matches the browser
  archive, including the observed LID namespace. This is a single smoke sample,
  not a live parity or end-to-end MCP latency benchmark.

## Remaining rollout and backlog

- Pilot ordinary incoming/group messages, media, provider interruption/recovery
  and sustained ingestion before replacing existing production writers. Preserve
  rollback image/session/config and existing owner leases. Real mutation checks
  need exact owner-approved recipients/content/actions.
- Native support is 43 of 49 current catalog entries; browser support is 45 of
  49. Four browser features stay explicitly unavailable: address-book add/delete
  and Business label read/replacement. Native adds global revoke and bounded
  phone-history requests. Unsupported capabilities are not silently emulated.
- Retained original payloads are required for quote/edit/revoke/forward/media
  operations; PostgreSQL-only old messages are still readable but may not be
  actionable until native history supplies their original payload.
- Rich URL previews, poll voting, newsletter/community contracts and expired
  media re-upload require separate work. Do not expose raw upstream methods as
  a shortcut around permission, policy or approval checks.
