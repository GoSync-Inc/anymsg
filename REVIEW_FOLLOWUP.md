# Agent review follow-up

This document preserves agent-review context. The current work queue and all
delivery status are maintained in **[BACKLOG.md](BACKLOG.md)**.
Installing a package or successfully reading a chat is not proof of complete history.

## Existing baseline

- Strict MCP/REST argument validation, explicit empty-result reasons and versioned
  subscription-filter reset already exist.
- Message/chat search defaults to compact results. Audio Ogg/Opus processing is
  supported through an explicitly configured ASR profile.
- File search includes current source-message captions and extraction/model text.
- Provider identity observations remain scoped. Transliteration produces candidates;
  similar names do not establish a shared Telegram/WhatsApp identity.
- The transport uses JSON POST requests and returns GET 405. There is no server
  notification stream; advertising list-change notifications requires implementing
  a real delivery path first. Clients should refresh discovery and compare the
  capability manifest after upgrades.

## Implemented baseline from the review

- Recent activity is the MCP/REST chat-overview default. `activity` and
  `last_activity_desc` are equivalent; explicit `id` traversal remains available.
- Reply hints default to a configurable 14-day window. Known bot senders are
  excluded in private chats. Explicit owner mentions/replies remain useful in groups
  of any size; unrelated recent chatter does not revive old attention.
- Cached Telegram entities provide names/types and bot flags without extra provider
  calls. Missing entities stay unknown. Observation enrichment preserves message
  revisions and never replaces a known title/type with a numeric/unknown fallback.
- Account sync status defaults to compact diagnostics, retaining coverage, alerts,
  freshness, operation states and file queue/error counts. Full detail and detailed
  per-chat status remain available.

Live deployment and provider acceptance must be recorded separately from these tests.

## Follow-up location

Recovery, storage, conflict, incoming-overview and file-analysis findings are
tracked as DK-02 through DK-06 and DK-11 through DK-14 in the central backlog.
Storage pauses protect collection; removing the reserve is not a capacity
solution. Competing observations must not be discarded to make status look
healthy. New provider messages and historical imports remain distinct.

Confirmed text sending is now implemented as an opt-in outbox: [SENDING.md](SENDING.md).
The acting agent obtains exact owner approval; a server approval workflow is future
work. Task extraction and project management remain outside this module.

Historical post-send feedback and implemented action boundaries are described in
[MESSAGE_ACTIONS.md](MESSAGE_ACTIONS.md). Sending, provenance, delivery/read
observations and supported expanded actions are already implemented. Optional
server approval is DK-27; a report does not change the owner's chosen mode.
