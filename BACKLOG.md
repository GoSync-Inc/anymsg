# DeedKit backlog

This is the single work queue for gateway, messaging, calendar and calls.
Update priorities and delivery status here. Module contracts, acceptance matrices
and historical plans retain detailed requirements, but do not maintain separate
queues. Tiden holds maintained behaviour specs and test evidence; its `Active`
status does not mean a feature passed live acceptance.

Reviewed **2026-10-16** (Asia/Makassar) against main
[`27ec8bf`](https://github.com/GoSync-Inc/deedkit/commit/27ec8bfc4d0a328dd4b639bab138cc21f6853ce7).
PRs #24, #27–33 and #35–39 are merged. PR #26 was closed without merging;
its backlog reconciliation is included in #31. The exact reviewed main has a
successful [CI run](https://github.com/GoSync-Inc/deedkit/actions/runs/38780132404).
The open code PRs at this snapshot are [#34](https://github.com/GoSync-Inc/deedkit/pull/34)
and draft [#40](https://github.com/GoSync-Inc/deedkit/pull/40).
The [audit matrix](docs/quality/agent-interface-audit-2026-10-15.md) maps each
reported defect to its source change and remaining acceptance. This reconciliation
uses merged source, PR records, main CI and a read-back of the two Tiden branches
in DK-31. It adds no provider, production or off-site recovery test. No fresh
deployment/readback receipt was collected for this main revision. GitHub Issues
had no open items; GitHub Projects were not rechecked.

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

Snapshot: **38 open entries** — 19 Planned, 6 Verify, 2 Triage, 2 Review
and 9 Optional. The delivered table contains **19 source increments**.
Seven former open IDs moved there without renumbering; six newly recorded
follow-ups are DK-40–45.

The next P1 acceptance pass is DK-02 (restore/capacity) and DK-33 (deployed audit
checks), with the native messaging, calendar and calls gates in DK-07/19/21.
DK-03/04/05/06 remain source work; merged diagnostics do not complete them.

| ID | Priority | Status | Work and completion criterion | Design / evidence |
| --- | --- | --- | --- | --- |
| DK-02 | P1 | Verify | Operational backup/restore and capacity acceptance. Record restoration of database and originals, receiver integrity and available capacity for the intended deployment. Local synthetic restore coverage already exists. | [Backup](BACKUP_STREAMING.md), [cutover](docs/operations/platform-cutover.md) |
| DK-03 | P1 | Planned | Cold-original lifecycle: archive before eviction, preserve text/evidence, add pin/recent/access rules and byte budgets; verify SHA-256 on rehydration and report expired/unavailable sources. Existing original-download demand does not implement eviction. | [Storage](MEDIA_STORAGE.md), [download increment](https://github.com/GoSync-Inc/deedkit/pull/25) |
| DK-04 | P1 | Planned | Durable history waves: execute the existing depth preview with fair chat scheduling, persisted checkpoints, restart recovery, provider backoff and visible gaps. | [History policy](HISTORY_POLICY.md) |
| DK-05 | P1 | Planned | Bounded recovery of already exhausted media jobs. Verify current sources, expose retry limits/backoff and outcomes; do not clear terminal failures or bypass storage admission blindly. PR #38 stops new local storage failures consuming provider attempts, but does not repair historical exhausted jobs or reclassify their recorded errors. | [Storage](MEDIA_STORAGE.md), [PR #38](https://github.com/GoSync-Inc/deedkit/pull/38) |
| DK-06 | P1 | Planned | Evidence-based conflict classification. Distinguish verified aliases, changed media descriptors and changed source content; preserve competing observations and audit resolutions. PR #38 makes the alert follow unresolved conflicts; classification/resolution remains separate. | [Identity contract](WHATSAPP_IDENTITIES.md), [PR #38](https://github.com/GoSync-Inc/deedkit/pull/38) |
| DK-07 | P1 | Verify | Native WhatsApp rollout: incoming/group messages, media, aliases, interruptions, ACK redelivery/backpressure and sustained ingestion; record approved mutation checks and an explicit cutover/rollback decision. Native implementation is already merged. | [Parity gates](docs/quality/whatsmeow-parity.md), [operator guide](docs/whatsapp-native.md) |
| DK-10 | P2 | Planned | Verified host receiver/onboarding and idle-agent wake-up. Integrate the host, re-read durable events after reconnect, and prove authentication, revocation and duplicate handling. Signed hint delivery alone does not prove a host turn; there is no agent onboarding-link API. | [Delivery boundaries](SUBSCRIPTIONS.md), DK-08/09 |
| DK-11 | P2 | Planned | Compact incoming overview across selected chats. Separate new provider messages from historical imports; retain ingestion-order ACKs and unknown provider unread/mute/archive state. Per-chat conversation views already exist. | [Review history](REVIEW_FOLLOWUP.md), [conversation contract](CONVERSATION.md) |
| DK-12 | P2 | Planned | Independent versioned file titles, summaries and structured analysis; reuse AI artifacts only for matching content/model/profile/version, retaining source links and partial/truncated states. Extraction and context-bound derivative reuse already exist. | [File index](FILE_INDEX.md), [enrichment](ENRICHMENT.md) |
| DK-13 | P2 | Planned | Measure local/API file-understanding profiles on synthetic or separately authorized files; record quality, cost and limits. Installing optional Codex support does not authorize execution. | [Enrichment](ENRICHMENT.md), [Codex adapter](CODEX_ENRICHMENT.md) |
| DK-14 | P3 | Optional | Bounded hybrid-retrieval experiment and measured performance work. Compare against PostgreSQL, including Russian/person/file-description queries; separate SQL, REST/MCP and connector latency. | [Search baseline](SEARCH.md) |
| DK-15 | P2 | Planned | Calendar incremental sync/cursors. Preserve coverage across required sources; missing/stale/partial data must never become verified free time. PR #29 already provides explicit bounded refresh jobs and source completeness, not incremental sync. | [Service contract](services/calendar/docs/SERVICE.md), [PR #29](https://github.com/GoSync-Inc/deedkit/pull/29) |
| DK-16 | P2 | Planned | Calendar service recurrence parity, including Google. Expose only supported, reconciled series/exception operations through MCP; legacy iCloud harness operations do not establish service support. | [Recurrence](services/calendar/docs/RECURRENCE.md) |
| DK-17 | P2 | Planned | Calendar guardians/assignment rules and activity-linked projections. Preview linked changes, preserve versions/partial receipts, and reconcile or compensate partial execution. Basic roles, bindings and assignments already exist. | [Service contract](services/calendar/docs/SERVICE.md), [calendar history](services/calendar/docs/ROADMAP.md) |
| DK-18 | P2 | Verify | Calendar multi-account family acceptance: a second independent Google account, cross-member grants and a Google+iCloud family scenario, including privacy and incomplete-coverage denial. Single-event CRUD has its own pilot scope. | [Validation](services/calendar/docs/VALIDATION.md), [platform criteria](docs/quality/platform-acceptance.md) |
| DK-19 | P1 | Verify | Calendar deployment/recovery acceptance: separate OS identities/secret access, vault/database restore, exactly one worker and independence from the original Mac. Deployment templates are already merged. | [Isolation limits](services/calendar/docs/SERVICE.md), [cutover](docs/operations/platform-cutover.md) |
| DK-20 | P2 | Planned | Calls owner notification channel, persistent controller and follow-up scheduler. Bind decisions/continuations to current terms and a fresh authorized brief. The browser decision queue already exists. | [Calls workflow](services/calls/README.md) |
| DK-21 | P1 | Verify | Live calls acceptance for configured Telegram/Twilio channels and browser Flows: ru/en/id behaviour, interruptions, decisions, latency, audible final speech and cleanup. Grok integration is merged (DK-37); its minimal-prompt audio comparison still awaits owner credential setup (DK-40). Connected messaging/synthetic audio do not prove working calls. | [Calls](services/calls/README.md), [Flows](services/calls/LIVE_FLOWS.md), [PR #34](https://github.com/GoSync-Inc/deedkit/pull/34) |
| DK-22 | P3 | Optional | Richer passive observations: reactions, polls/votes, albums/topics and quoted content, with bounded refresh and explicit revision/coverage rules. Live queries do not populate a complete passive archive. | [Capability reference](MESSENGER_CAPABILITY_AUDIT.md) |
| DK-23 | P3 | Optional | Profile pictures/contact details and permitted profile/group-picture changes. Define explicit scopes and bounded registered-image inputs; separate About text from Status stories. | [Capability reference](MESSENGER_CAPABILITY_AUDIT.md) |
| DK-24 | P3 | Optional | Group settings, restrictions, slow mode and admission requests. Add typed, separately authorized effects and partial/unknown outcomes; no unrestricted administrator payloads. | [Capability reference](MESSENGER_CAPABILITY_AUDIT.md) |
| DK-25 | P3 | Optional | Join/invitation/channel/forum operations. Require explicit destination grants; newly joined content must not become visible through old chat-scoped keys. | [Capability reference](MESSENGER_CAPABILITY_AUDIT.md) |
| DK-26 | P3 | Optional | Albums, scheduling, typing indicators, richer media edits and Telegram privacy controls. Define bounded high-level variants and fresh grants instead of reflecting provider methods. | [Capability reference](MESSENGER_CAPABILITY_AUDIT.md) |
| DK-27 | P3 | Optional | Server-managed message drafts/approvals. If enabled by the owner, require exact-payload, expiring, single-use approval that agent keys cannot issue. Acting-agent approval remains the baseline. | [Sending](SENDING.md), [action reference](MESSAGE_ACTIONS.md) |
| DK-28 | P3 | Optional | Additional native WhatsApp capabilities: rich URL previews, poll voting, newsletter/community contracts, expired-media re-upload and unsupported address-book/Business-label effects. Review each extension separately; browser support is not native parity. | [Native limits](docs/plans/2026-10-15-whatsmeow-primary.md#rollout-and-capability-reference) |
| DK-29 | P2 | Planned | Publish the next reviewed package release. Choose its version explicitly, verify outside-checkout installation/distribution contents, preserve package/provider/schema identities and record registry publication. Main documents source 0.9.19/schema 32; the latest GitHub release checked here is v0.9.9. A main merge or container promotion is not package publication; registries were not rechecked. | [Release procedure](RELEASING.md), [GitHub releases](https://github.com/GoSync-Inc/deedkit/releases) |
| DK-30 | P3 | Optional | Authenticated MCP discovery notifications. Implement a real bounded stream with revocation/restart/client compatibility evidence before advertising list-change support. JSON POST with GET 405 remains the current transport. | [Discovery reference](MESSAGE_ACTIONS.md#client-discovery) |
| DK-31 | P2 | Triage | Both named Tiden branches were read back as `open` on 2026-10-16. Reconcile their spec diffs and test evidence with the merged baseline, then obtain the owner's spec-review decision. Git PR merges do not merge Tiden reviews; no review was closed in this reconciliation. | [Telemetry review](https://app.tiden.ai/1dc18e09-4a09-4d74-8300-cd4302622357/products/75bd0580-2af4-4d5a-b236-c5a9d7ff7ce1/branches/da177d48-8494-44fc-99fc-7d006df55750), [subscription recovery review](https://app.tiden.ai/1dc18e09-4a09-4d74-8300-cd4302622357/products/75bd0580-2af4-4d5a-b236-c5a9d7ff7ce1/branches/e4481216-b911-46f5-ba72-fff623da3657) |
| DK-33 | P1 | Verify | Operational audit acceptance after verified deployment: reconnect the consuming host and compare its tools; measure scoped query latency and compact/transcript response bytes; verify WhatsApp live capture separately from history; inspect capacity, storage paths, spool growth and old exhausted media jobs; review calls grants/provider readiness and an explicitly authorized receiver. Recheck readable titles, liveness, self-chat receipts and unresolved-conflict alerts. Historical sends stay unknown without exact receipts. Record each outcome without blanket queue resets or new grants. Source fixes #29/#31/#33/#35/#37–39 are merged; this is their remaining deployed acceptance. | [Audit matrix](docs/quality/agent-interface-audit-2026-10-15.md), [collector states](COLLECTOR_STATES.md), DK-02/05/07/10/21 |
| DK-35 | P2 | Planned | Owner-reviewed calendar primary/duplicate mapping and complete source inventories. Preserve provider resource identity; equal labels or timezones never imply duplicates. Bind additional sources only on explicit owner selection; missing inventory remains unknown. | [Calendar service](services/calendar/docs/SERVICE.md), DK-15/18 |
| DK-36 | P2 | Planned | Silence/expected-reply timers, quiet hours, debounce and batching. Define clock/restart/revocation/idempotency rules and per-key delivery budgets before enabling automatic wake scheduling. Native reply evidence and durable hints are DK-09. | [Subscription boundaries](SUBSCRIPTIONS.md) |
| DK-39 | P2 | Planned | Remaining agent-feedback items from 2026-10-15: reply-queue weight for closing courtesies, own accounts and service senders; `chat_types` in search; `reply_to: {id, snippet}`; chat title/time in `search_files` and `search_people`; agenda local time and calendar labels; limit clamping and an explicit unknown-`chat_id` error. The queue speed, titles, diagnostics, annotations, counters and ranking fixes are in PR #35. | [PR #35](https://github.com/GoSync-Inc/deedkit/pull/35), DK-11/34 |
| DK-40 | P3 | Review | Opt-in minimal Grok browser prompt comparison. PR #34 is open, so this experiment is not in main. Review/merge separately; retain the guided default, scoped tools and owner decisions. An actual audio comparison awaits owner credential setup and belongs to DK-21. | [PR #34](https://github.com/GoSync-Inc/deedkit/pull/34) |
| DK-41 | P1 | Review | Verified blue-green HTTP releases: draft PR #40 implements paired messaging API/gateway readiness, routing handoff, stream draining, identity fences and recovery inspection. Review and merge the opt-in path, then record explicitly authorized enrollment/rollback acceptance. Current main still stops selected services before replacement. Provider owners, calendar and voice retain their existing promotion paths. | [Draft PR #40](https://github.com/GoSync-Inc/deedkit/pull/40), [current promotion contract](docs/operations/production.md#apply-and-verify) |
| DK-42 | P2 | Planned | Narrow messaging database roles after schema 32. Replace direct indexer/enricher writes to `events`/`feed_counter` with a constrained publication path; isolate collectors by account. Add a new migration and negative-permission checks without rewriting old migration checksums. Explicitly deferred in PR #37; current role templates still grant these writes. | [PR #37](https://github.com/GoSync-Inc/deedkit/pull/37), [indexer role](services/messaging/messaging_service/indexer_role.sql), [enricher role](services/messaging/messaging_service/enrichment_role.sql), [collector role](services/messaging/messaging_service/collector_role.sql) |
| DK-43 | P2 | Planned | Remaining collector edge cases from PR #37: actionable temporary-ban/replaced-session/outdated-client status instead of generic relink guidance; expose native `capture_skips`; prevent a long media transfer blocking reconciliation; isolate bad items within browser history pages; bound oversized dialog catalogs without losing continuation/coverage. The merged poison-item, transient-DB and history-backpressure fixes do not complete these follow-ups. | [PR #37 follow-ups](https://github.com/GoSync-Inc/deedkit/pull/37), [collector contract](COLLECTORS.md) |
| DK-44 | P2 | Planned | Correct instant-event mutation previews: `plan().before.end` must use the source instant rather than the internal +1 microsecond overlap sentinel. Agenda already hides that sentinel; verify preview/diff and apply agree without changing provider identity or recurrence scope. | [PR #37 follow-ups](https://github.com/GoSync-Inc/deedkit/pull/37), [calendar core](services/calendar/family_calendar/service/core.py) |
| DK-45 | P2 | Triage | Resolve remaining subscription review decisions: read-only tool annotations versus page-receipt creation, the lock/latency impact of outbound hint HTTP while preserving revocation fences, and bounded retention/pruning. PR #33 fixes receipt eviction and TLS retry, not these design questions; record the accepted contract and evidence before closing. | [PR #33 review notes](https://github.com/GoSync-Inc/deedkit/pull/33), [delivery source](services/messaging/messaging_service/event_delivery.py), [subscription boundaries](SUBSCRIPTIONS.md#remaining-extensions-and-verification-boundaries) |

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
| DONE-08 | Done | Passive durable subscriptions, lifecycle/version checks and independent file-readiness events. | [Implemented contract](SUBSCRIPTIONS.md), [0.9.16 baseline](https://github.com/GoSync-Inc/deedkit/pull/10). ACK and signed hints are now merged in DK-08/09 below; verified host turns remain DK-10. |
| DONE-09 | Done | Opt-in text/reply sends, formatting, passive delivery/read observations and the allowlisted live operation catalog, including supported file/edit/reaction/read actions. | [Sending](SENDING.md), [formatting](FORMATTING.md), [receipts](RECEIPTS.md), [catalog](MESSENGER_OPERATIONS.md). Exact support is per provider/account. |
| DK-01 | Done | Calls owner authentication and bounded Twilio callbacks; subsequent process-only authenticated health checks, disabled-by-default external control MCP and safe control-token creation/validation. | [PR #24](https://github.com/GoSync-Inc/deedkit/pull/24), [#39](https://github.com/GoSync-Inc/deedkit/pull/39). Live calls acceptance remains DK-21. |
| DK-08 | Done | Schema-32 exact-key subscription ACK with issued page receipts, scope/version checks and separate consumption positions. New pages evict old receipts within the cap instead of locking out polling. Passive reads do not ACK. | [PR #31](https://github.com/GoSync-Inc/deedkit/pull/31), [#33](https://github.com/GoSync-Inc/deedkit/pull/33), [contract](SUBSCRIPTIONS.md). Deployed acceptance remains DK-33; review decisions DK-45. |
| DK-09 | Done | Durable signed hints, bounded waiting, incoming/mention/exact owner-reply/confirmed-send-reply presets and bounded TLS retry. | [PR #31](https://github.com/GoSync-Inc/deedkit/pull/31), [#33](https://github.com/GoSync-Inc/deedkit/pull/33), [contract](SUBSCRIPTIONS.md). Receiver/deployment acceptance DK-33; actual host turns DK-10; silence/batching DK-36. |
| DK-32 | Done | First audit source fixes: scoped chat queries, name ranking, capture-qualified freshness, file metadata/names, conflict guidance, discovery/identity diagnostics, bounded send observation, private feedback, calendar diffs/constraints/refresh/source completeness. Post-enqueue wait failures now preserve the queued send ID. | [PR #29](https://github.com/GoSync-Inc/deedkit/pull/29), [#31](https://github.com/GoSync-Inc/deedkit/pull/31), [#33](https://github.com/GoSync-Inc/deedkit/pull/33). Operational evidence remains DK-33; complete calendar owner mapping DK-35. |
| DK-34 | Done | Conversation compact returns each message text once with conditional qualifiers; `detail=transcript` returns the numbered transcript with cursors/file references. Full and archive compact remain compatible. Also merged: faster reply queue, readable WhatsApp/self-chat titles, accurate waiting reasons, actionable gateway failures, calls catalog/listing, available-action filtering, disjoint file counters and phrase ranking. | [PR #35](https://github.com/GoSync-Inc/deedkit/pull/35), [contract](CONVERSATION.md#detail-levels). Deployed latency/response-size measurement remains DK-33; residual feedback DK-39. |
| DK-37 | Done | Experimental Grok voice transport and scoped phone readiness. This source integration is distinct from the unmerged minimal-prompt experiment. | [PR #28](https://github.com/GoSync-Inc/deedkit/pull/28). Live acceptance DK-21; experiment DK-40. |
| DK-38 | Done | Existing conversation baseline already supplies human-readable unknown/service labels, retains provider type/uncertainty, and excludes only observed service events. An empty body alone does not establish service semantics. | [Presentation source](services/messaging/messaging_service/conversation.py), [provider metadata checks](tests/test_conversation_provider_metadata.py), [label/filter checks](tests/test_conversation_queries.py), DONE-01. Additional native rich types remain DK-22. |
| DONE-10 | Done | Verified exact-main promotion, packaged existing Telegram session owner, unprivileged-image source readability, source-ref/tag guards and automatic Tiden CI routing for unmapped feature branches. | [PR #27](https://github.com/GoSync-Inc/deedkit/pull/27), [#30](https://github.com/GoSync-Inc/deedkit/pull/30), [#32](https://github.com/GoSync-Inc/deedkit/pull/32), [#36](https://github.com/GoSync-Inc/deedkit/pull/36), [#37](https://github.com/GoSync-Inc/deedkit/pull/37). Recovery gates DK-02/19; seamless promotion DK-41. |
| DONE-11 | Done | Service-audit fixes: poison-item quarantine and bounded provider normalization, transient-DB retries, send-not-started outcomes, size-aware media deadlines/temporary-file cleanup, history backpressure, bounded calendar recurrence/startup recovery, principal-specific gateway limits, long-download integrity/access checks, interrupted-backup cleanup and deployment healthchecks. | [PR #37](https://github.com/GoSync-Inc/deedkit/pull/37), reviewed-main CI above. Operational acceptance DK-33; explicitly deferred source work DK-42/43/44. |
| DONE-12 | Done | Evening-audit source fixes: liveness independent of incoming traffic/history backlog, separate history-spool accounting, unresolved-only conflict alerts, self-chat receipts `not_applicable` (including owner LID), and local storage failures distinguished from network errors without consuming provider attempts. | [PR #38](https://github.com/GoSync-Inc/deedkit/pull/38), [states](COLLECTOR_STATES.md), [receipts](RECEIPTS.md), [storage](MEDIA_STORAGE.md). Actual disk/spool recovery DK-33; exhausted jobs DK-05; conflict classification DK-06. |

## Tiden reconciliation

During the original reconciliation, four specs incorrectly left in `Backlog`
were recorded as implemented contracts with the following workflow status:

| Spec | Existing identity | Delivery evidence / workflow status |
| --- | --- | --- |
| 199 — versioned conversation views | `cc555016-a082-453e-8f23-87f1128b2856` | DONE-01; `Active` |
| 200 — explicit conversation search | `b74d6111-7e3b-489f-b621-20d09f3db760` | DONE-01; `Active`; REST/SDK defaults to archive, MCP to conversation |
| 201 — uncertainty-qualified actual chat head | `fd83f0d9-2c10-4a9f-8210-b1931d314795` | DONE-01; `Active` |
| 214 — bounded original-download requests | `48f13d49-ed88-4e99-9fdc-7e77e09f4147` | DONE-07; `Active` |

Read-back after the original backlog reconciliation: **212 Active specs, zero Backlog specs**.
This is a historical workflow count, not a fresh verification verdict for the audit branch.
Future work is tracked in the open queue above.

That reconciliation changed workflow status and corrected spec 200's ambiguous
transport-default wording. This status refresh did not edit specs or merge Tiden
branches. Preserve spec/test IDs, links and historical source
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
