# Agent review follow-up

This checklist separates implemented contracts from remaining source recovery.
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

## Implemented in the current candidate

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

## Remaining priority order

1. Resolve backup restore/capacity acceptance and cold-file lifecycle. Storage pauses
   protect collection; removing the reserve is not a capacity solution.
2. Durable history recovery/waves and bounded media recovery. Retryable failures
   already receive capped retries; exhausted jobs need an explicit bounded recovery
   policy with current-source verification, backoff and visibility.
3. Investigate version-conflict categories without discarding competing evidence.
   Equivalent confirmed aliases, changing media descriptors and truly changed source
   content require different decisions; a connected session does not resolve them.
4. A compact, chronological incoming overview across selected chats, with clear
   separation between new provider messages and historical imports. Preserve the
   ingestion-order ACK contract. Provider unread/mute/archive values remain unknown
   until reliably observed; archive ACK is a separate concept.
5. Versioned file titles/summaries/detailed analysis and measured local/API profiles,
   followed by connector and hybrid-retrieval experiments in ROADMAP.md.

Confirmed text sending is now implemented as an opt-in outbox: [SENDING.md](SENDING.md).
The acting agent obtains exact owner approval; a server approval workflow is future
work. Task extraction and project management remain outside this module.
