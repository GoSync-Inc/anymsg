# Roadmap

This library stays focused on messenger collection and an agent interface.
Task/project management is outside the archive. Version 0.9.13 adds passive
delivery/read observations and bounded status updates ([RECEIPTS.md](RECEIPTS.md)).
Version 0.9.12 completes missing
Telegram private-send identities and exposes confirmed agent-send provenance.
[MESSAGE_ACTIONS.md](MESSAGE_ACTIONS.md) tracks receipt/reaction/action follow-up,
including discovery compatibility. [FORMATTING.md](FORMATTING.md) adds the planned
rich-text preview/rendering contract; formatted sends are not yet available.
Version 0.9.11 separates the
WhatsApp sending lane from background history/media in the same authenticated
collector. Version 0.9.10 adds opt-in text
sending as a separate, explicitly approved action boundary.

The 0.9.9 baseline already includes per-chat evidence, explicit collector errors,
verified WhatsApp identity observations, file-text indexing, morphology search,
bounded downloads, byte-level deduplication and optional enrichment adapters.
These capabilities do not imply that every source history or file is available.

Next work, in order:

1. **History recovery:** durable fair scheduling across chats, selectable setup
   depth (new only or a historical period), and waves from a week through a month,
   quarter and year. The current planner is a preview; execution, checkpoint
   recovery and provider backoff still need implementation and live acceptance.
2. **Storage lifecycle:** retain extracted text/evidence while optionally evicting
   cold originals; configurable pin/recent/frequent-access rules and byte budgets.
   Demand re-download must verify the original digest and explicitly report
   expired or unavailable provider references. Never promise rehydration of a
   source that no longer exists. SHA-256 blob deduplication is already implemented.
3. **File understanding:** preserve full text/segments and separate descriptions,
   titles and structured analysis as versioned artifacts. Reuse analysis for exact
   duplicates with matching model/profile/version, keep source links and expose
   partial/truncated results. Local CPU and API adapters remain configurable;
   Codex CLI is optional, must be explicitly authorized and is not activated by
   installing the package. Measure cost/quality on synthetic or owner-approved files.
4. **Connector evaluation:** test whatsmeow separately before considering a
   WhatsApp transport replacement. No session migration or production switch is
   implied. Continue testing identity mappings, reconnect and history gaps.
5. **Retrieval and performance:** benchmark a bounded hybrid search experiment
   against the existing PostgreSQL baseline, including Russian queries, people
   and file descriptions. Measure SQL, REST/MCP and client-connector latency
   separately; improve only demonstrated bottlenecks.
6. **Sending:** opt-in text/reply outbox with idempotency is implemented (SENDING.md).
   Next: previewed formatting and reaction reads,
   then attachments/edit/delete/reaction/read actions. A scoped server draft/approval
   path is optional and follows explicit owner configuration. Ambiguous sends
   must never be blindly retried; formatted text must not be silently split.

Every stage needs regression tests, resource/coverage diagnostics and explicit
release notes. Separate owners should use separate deployments; true tenancy is
not part of the present security model. The package is experimental, with source
and installation guides intended for operator-managed deployments.
