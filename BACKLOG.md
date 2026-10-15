# DeedKit backlog

This is the single work queue for gateway, messaging, calendar and calls.
Update priorities and delivery status here. Module contracts, acceptance matrices
and historical plans retain detailed requirements, but do not maintain separate
queues. Tiden holds maintained behaviour specs and test evidence; its `Active`
status does not mean a feature passed live acceptance.

Reviewed **2026-10-15** against main
[`7c98979`](https://github.com/GoSync-Inc/deedkit/commit/7c98979), merged PRs,
open PR #24 and the Tiden main spec tree. This is a source review, not a new
provider, production or off-site recovery test. GitHub Issues had no open items;
GitHub Projects could not be inspected with the available token.

## Status and priority

- **Planned**: implementation remains; related foundations may already exist.
- **Review**: an open PR exists; implementation is not yet merged.
- **Verify**: an implementation or pilot exists, but the named acceptance evidence
  is still required. This does not assert that a deployment has failed.
- **Triage**: an open record needs reconciliation before its delivery status is known.
- **Optional**: a conditional extension or experiment, not a delivery commitment.
- **Done**: the stated source increment is merged. Separate operational acceptance
  remains open where listed below.

**P1** covers data preservation, recovery, access and rollout acceptance;
**P2** covers subsequent product increments; **P3** covers optional expansion
and experiments. Priority follows the existing recovery-first review; it is not
an estimate or assignment. Entries within a priority are not a strict schedule.

## Open work

| ID | Priority | Status | Work and completion criterion | Design / evidence |
| --- | --- | --- | --- | --- |
| DK-01 | P1 | Review | Calls owner authentication and bounded Twilio callbacks. Merge the reviewed change and retain its actual CI evidence; a green branch check alone is not a merge. | [PR #24](https://github.com/GoSync-Inc/deedkit/pull/24) |
| DK-02 | P1 | Verify | Operational backup/restore and capacity acceptance. Record restoration of database and originals, receiver integrity and available capacity for the intended deployment. Local synthetic restore coverage already exists. | [Backup](BACKUP_STREAMING.md), [cutover](docs/operations/platform-cutover.md) |
| DK-03 | P1 | Planned | Cold-original lifecycle: archive before eviction, preserve text/evidence, add pin/recent/access rules and byte budgets; verify SHA-256 on rehydration and report expired/unavailable sources. Existing original-download demand does not implement eviction. | [Storage](MEDIA_STORAGE.md), [download increment](https://github.com/GoSync-Inc/deedkit/pull/25) |
| DK-04 | P1 | Planned | Durable history waves: execute the existing depth preview with fair chat scheduling, persisted checkpoints, restart recovery, provider backoff and visible gaps. | [History policy](HISTORY_POLICY.md) |
| DK-05 | P1 | Planned | Bounded recovery of exhausted media jobs. Verify current sources, expose retry limits/backoff and outcomes; do not clear terminal failures or bypass storage admission blindly. Explicit demand and capped ordinary retries already exist. | [Collectors](COLLECTORS.md), [storage](MEDIA_STORAGE.md) |
| DK-06 | P1 | Planned | Evidence-based conflict classification. Distinguish verified aliases, changed media descriptors and changed source content; preserve competing observations and audit resolutions. | [Review history](REVIEW_FOLLOWUP.md), [identity contract](WHATSAPP_IDENTITIES.md) |
| DK-07 | P1 | Verify | Native WhatsApp rollout: incoming/group messages, media, aliases, interruptions, ACK redelivery/backpressure and sustained ingestion; record approved mutation checks and an explicit cutover/rollback decision. Native implementation is already merged. | [Parity gates](docs/quality/whatsmeow-parity.md), [operator guide](docs/whatsapp-native.md) |
| DK-08 | P2 | Planned | Subscription event ACK: credential-owned, scope/version-checked consumption positions; passive reads must not acknowledge events or another agent's work. | [Subscription design](SUBSCRIPTIONS.md) |
| DK-09 | P2 | Planned | Subscription dispatch, batching and reply/mention presets. Preserve durable matching, exact evidence, limits and restart behaviour; acknowledged consumption depends on DK-08. | [Subscription design](SUBSCRIPTIONS.md) |
| DK-10 | P2 | Planned | Verified host receiver, notification delivery and agent wake-up. Re-read durable events after reconnect; prove authentication, revocation, bounded delivery and duplicate handling. Passive subscriptions cannot wake an idle agent today. | [Delivery design](SUBSCRIPTIONS.md#planned-delivery-architecture) |
| DK-11 | P2 | Planned | Compact incoming overview across selected chats. Separate new provider messages from historical imports; retain ingestion-order ACKs and unknown provider unread/mute/archive state. Per-chat conversation views already exist. | [Review history](REVIEW_FOLLOWUP.md), [conversation contract](CONVERSATION.md) |
| DK-12 | P2 | Planned | Independent versioned file titles, summaries and structured analysis; reuse AI artifacts only for matching content/model/profile/version, retaining source links and partial/truncated states. Extraction and context-bound derivative reuse already exist. | [File index](FILE_INDEX.md), [enrichment](ENRICHMENT.md) |
| DK-13 | P2 | Planned | Measure local/API file-understanding profiles on synthetic or separately authorized files; record quality, cost and limits. Installing optional Codex support does not authorize execution. | [Enrichment](ENRICHMENT.md), [Codex adapter](CODEX_ENRICHMENT.md) |
| DK-14 | P3 | Optional | Bounded hybrid-retrieval experiment and measured performance work. Compare against PostgreSQL, including Russian/person/file-description queries; separate SQL, REST/MCP and connector latency. | [Search baseline](SEARCH.md) |
| DK-15 | P2 | Planned | Calendar incremental sync/cursors and freshness. Preserve coverage across required sources; missing/stale/partial data must never become verified free time. Bounded polling and agenda/slots already exist. | [Service contract](services/calendar/docs/SERVICE.md) |
| DK-16 | P2 | Planned | Calendar service recurrence parity, including Google. Expose only supported, reconciled series/exception operations through MCP; legacy iCloud harness operations do not establish service support. | [Recurrence](services/calendar/docs/RECURRENCE.md) |
| DK-17 | P2 | Planned | Calendar guardians/assignment rules and activity-linked projections. Preview linked changes, preserve versions/partial receipts, and reconcile or compensate partial execution. Basic roles, bindings and assignments already exist. | [Service contract](services/calendar/docs/SERVICE.md), [calendar history](services/calendar/docs/ROADMAP.md) |
| DK-18 | P2 | Verify | Calendar multi-account family acceptance: a second independent Google account, cross-member grants and a Google+iCloud family scenario, including privacy and incomplete-coverage denial. Single-event CRUD has its own pilot scope. | [Validation](services/calendar/docs/VALIDATION.md), [platform criteria](docs/quality/platform-acceptance.md) |
| DK-19 | P1 | Verify | Calendar deployment/recovery acceptance: separate OS identities/secret access, vault/database restore, exactly one worker and independence from the original Mac. Deployment templates are already merged. | [Isolation limits](services/calendar/docs/SERVICE.md), [cutover](docs/operations/platform-cutover.md) |
| DK-20 | P2 | Planned | Calls owner notification channel, persistent controller and follow-up scheduler. Bind decisions/continuations to current terms and a fresh authorized brief. The browser decision queue already exists. | [Calls workflow](services/calls/README.md) |
| DK-21 | P1 | Verify | Live calls acceptance for configured Telegram/Twilio channels and browser Flows: ru/en/id behaviour, interruptions, decisions, latency, audible final speech and cleanup. Connected messaging/synthetic audio do not prove working calls. | [Calls](services/calls/README.md), [Flows](services/calls/LIVE_FLOWS.md) |
| DK-22 | P3 | Optional | Richer passive observations: reactions, polls/votes, albums/topics and quoted content, with bounded refresh and explicit revision/coverage rules. Live queries do not populate a complete passive archive. | [Capability reference](MESSENGER_CAPABILITY_AUDIT.md) |
| DK-23 | P3 | Optional | Profile pictures/contact details and permitted profile/group-picture changes. Define explicit scopes and bounded registered-image inputs; separate About text from Status stories. | [Capability reference](MESSENGER_CAPABILITY_AUDIT.md) |
| DK-24 | P3 | Optional | Group settings, restrictions, slow mode and admission requests. Add typed, separately authorized effects and partial/unknown outcomes; no unrestricted administrator payloads. | [Capability reference](MESSENGER_CAPABILITY_AUDIT.md) |
| DK-25 | P3 | Optional | Join/invitation/channel/forum operations. Require explicit destination grants; newly joined content must not become visible through old chat-scoped keys. | [Capability reference](MESSENGER_CAPABILITY_AUDIT.md) |
| DK-26 | P3 | Optional | Albums, scheduling, typing indicators, richer media edits and Telegram privacy controls. Define bounded high-level variants and fresh grants instead of reflecting provider methods. | [Capability reference](MESSENGER_CAPABILITY_AUDIT.md) |
| DK-27 | P3 | Optional | Server-managed message drafts/approvals. If enabled by the owner, require exact-payload, expiring, single-use approval that agent keys cannot issue. Acting-agent approval remains the baseline. | [Sending](SENDING.md), [action reference](MESSAGE_ACTIONS.md) |
| DK-28 | P3 | Optional | Additional native WhatsApp capabilities: rich URL previews, poll voting, newsletter/community contracts, expired-media re-upload and unsupported address-book/Business-label effects. Review each extension separately; browser support is not native parity. | [Native limits](docs/plans/2026-10-15-whatsmeow-primary.md#rollout-and-capability-reference) |
| DK-29 | P2 | Planned | Publish the next reviewed package release. Choose its version explicitly, verify outside-checkout installation/distribution contents, preserve package/provider/schema identities and record publication. Development source leads published 0.9.9. | [Release procedure](RELEASING.md), [versions](README.md) |
| DK-30 | P3 | Optional | Authenticated MCP discovery notifications. Implement a real bounded stream with revocation/restart/client compatibility evidence before advertising list-change support. JSON POST with GET 405 remains the current transport. | [Discovery reference](MESSAGE_ACTIONS.md#client-discovery) |
| DK-31 | P2 | Triage | Reconcile open Tiden telemetry and subscription-recovery reviews with main and test evidence. Preserve IDs/links; do not merge or close an unrelated review merely because baseline code exists. | `codex/tiden-runtime-telemetry`; `intent/2026-10-14-agents-maintain-durable` |

## Delivered source increments

These are removed from future work. `Done` applies to the stated increment,
not every upstream method or an unrecorded production deployment.

| ID | Status | Delivered scope | Evidence / remaining boundary |
| --- | --- | --- | --- |
| DONE-01 | Done | Conversation views, message kinds, qualified ordering and actual archived last-turn state, including durable WhatsApp metadata. | [PR #14](https://github.com/GoSync-Inc/deedkit/pull/14), [#15](https://github.com/GoSync-Inc/deedkit/pull/15), [contract](CONVERSATION.md). Cross-chat overview remains DK-11. |
| DONE-02 | Done | Independent gateway, messaging, calendar and calls modules, scoped common MCP, profiles and deployment tooling. | [PR #17](https://github.com/GoSync-Inc/deedkit/pull/17), [#18](https://github.com/GoSync-Inc/deedkit/pull/18). Operational gates remain DK-02/19/21. |
| DONE-03 | Done | Native whatsmeow provider, selected for new configurations, with explicit browser fallback. | [PR #19](https://github.com/GoSync-Inc/deedkit/pull/19). Existing-account rollout remains DK-07. |
| DONE-04 | Done | Verified native send receipts, graceful IPC shutdown and provider-aware capability discovery. | [PR #20](https://github.com/GoSync-Inc/deedkit/pull/20), [#21](https://github.com/GoSync-Inc/deedkit/pull/21). Unsupported actions stay hidden/denied. |
| DONE-05 | Done | Scoped agent file uploads and WhatsApp send reconciliation. | [PR #22](https://github.com/GoSync-Inc/deedkit/pull/22). Staging bytes does not send or approve them. |
| DONE-06 | Done | Native capture-spool integrity, verified identity handling and upload/media lock-order correction. | [PR #23](https://github.com/GoSync-Inc/deedkit/pull/23). Live rollout remains DK-07. |
| DONE-07 | Done | Seven-day default original prefetch and bounded scoped `request_file_download`, pending-file metadata and demand expiry/quotas. | [PR #25](https://github.com/GoSync-Inc/deedkit/pull/25), schema 31. Cold eviction/history waves remain DK-03/04. |
| DONE-08 | Done | Passive durable subscriptions, lifecycle/version checks and independent file-readiness events. | [Implemented contract](SUBSCRIPTIONS.md), [0.9.16 baseline](https://github.com/GoSync-Inc/deedkit/pull/10). ACK, push and wake-up remain DK-08/09/10. |
| DONE-09 | Done | Opt-in text/reply sends, formatting, passive delivery/read observations and the allowlisted live operation catalog, including supported file/edit/reaction/read actions. | [Sending](SENDING.md), [formatting](FORMATTING.md), [receipts](RECEIPTS.md), [catalog](MESSENGER_OPERATIONS.md). Exact support is per provider/account. |

## Tiden reconciliation

The four specs incorrectly left in `Backlog` describe implemented contracts:

| Spec | Existing identity | Delivery evidence / workflow status |
| --- | --- | --- |
| 199 — versioned conversation views | `cc555016-a082-453e-8f23-87f1128b2856` | DONE-01; `Active` |
| 200 — explicit conversation search | `b74d6111-7e3b-489f-b621-20d09f3db760` | DONE-01; `Active`; REST/SDK defaults to archive, MCP to conversation |
| 201 — uncertainty-qualified actual chat head | `fd83f0d9-2c10-4a9f-8210-b1931d314795` | DONE-01; `Active` |
| 214 — bounded original-download requests | `48f13d49-ed88-4e99-9fdc-7e77e09f4147` | DONE-07; `Active` |

Read-back after reconciliation: **212 Active specs, zero Backlog specs**.
Future work is tracked in the open queue above.

Reconciliation changes workflow status and corrects spec 200's ambiguous
transport-default wording. Preserve spec/test IDs, links and historical source
anchors. This does not set verification to PASS, fabricate test results or turn
future work into implemented specs. See [Tiden evidence rules](TIDEN-CI.md).

## Maintenance

1. Add work here with a stable ID, priority, status, completion criterion and
   source link. Keep IDs when reordering; never recycle a delivered ID.
2. When a PR opens, set `Review` and link it. After merge, move its implemented
   scope to the delivered table. Keep missing operational/live proof as `Verify`.
3. Check current main and the actual PR state before changing status. Record the
   reviewed date/revision; a plan, active spec or green test alone is not delivery.
4. Keep detailed contracts/acceptance procedures in module documents; link back
   here for priorities. Do not recreate module-specific work queues.
5. Test with synthetic fixtures. Provider actions, account cutover and publication
   follow their existing explicit authorization boundaries.

Task/project management inside the archive, true multitenancy, raw provider RPC,
ownership transfer and payments remain outside scope. Windows server and Docker
ARM support are not claimed.
