# Agent interface audit reconciliation

Source review date: 2026-10-15. This matrix records the implementation scope
derived from an external agent audit. The private report and runtime identifiers
are not repository inputs. Source changes require review and deployment; synthetic
tests do not establish production recovery or host acceptance.

Priorities and follow-up status belong in [BACKLOG.md](../../BACKLOG.md).

| Finding | Source change in this review | Acceptance still required |
| --- | --- | --- |
| B1: chat listing times out | Page before expensive presentation; share scoped attention candidates and reply heads; cover populated private/group histories and dense replies. | Verify the deployed archive's read latency and query plans. |
| B2: calls preparation denied | Gateway capabilities distinguish current gateway grants and backend identity; preserve safe request/error diagnostics and anti-enumeration. | Owner reviews the intended calls grants and configured provider. This change grants no rights. |
| B3: catalog differs from host tools | Effective catalog count/hash, selected tools and policy revision agree; unavailable discovery streaming is explicit. | Reconnect the consuming host and compare its actual tools/list. |
| B4: historical send is unknown | Optional bounded send observation; no duplicate enqueue or automatic retry; retain unknown without exact dispatch receipt evidence. | Recover a historical send only from an exact provider receipt. Body/time similarity is insufficient. |
| B5: recent scan hides capture backlog | Freshness is qualified by capture backlog, pause and lease evidence; quiet sources do not imply missing ingestion. | Drain the actual capture spool and verify current incoming ingestion after deployment. |
| B6: nonexistent conflict action | Existing get_context is the inspection path in public guidance. | Review real conflicts using preserved observations; this change does not silently resolve them. |
| B7: extraction and names disagree | Current-revision extraction coverage agrees across attachment/text/file APIs; generated display names use verified provider/kind/MIME evidence. Original provider descriptors remain intact. | Re-read affected files after deployment; extraction accuracy remains a separate measurement. |
| B8: media paused or exhausted | Distinguish active/expired leases, storage pause, queued retries and exhausted failures; keep bounded explicit download demand. | Check disk capacity, source availability and network errors before any bounded recovery. No admission bypass or bulk reset. |
| B9: partial calendars appear fully free | Expose checked source selection, completeness and candidate intervals; an incomplete member inventory remains unknown. | Owner explicitly selects intended sources. Never infer duplicate calendars or add bindings by display name. |
| B10: gateway/backend identities differ | Label both identities and current grants explicitly. Existing package/provider/renderer identities remain stable. | Confirm the intended credential mapping on the deployed host. |
| B11: substring outranks exact name | Exact and token matches precede transliterated substrings; pagination binds to the normalized query and scope. | Confirm useful ranking on the authorized deployed scope. |

## Additional implemented interfaces

- Explicit fetch/ACK with issued page receipts, key ownership, scope/version
  checks and idempotent consumption; passive reads do not ACK.
- Bounded event waiting and evidence-based incoming/mention/owner-reply/
  confirmed-send-reply wake reasons. Native Telegram/WhatsApp reply formats have
  synthetic capture-to-match coverage.
- Operator-registered HTTPS receivers, content-free signed hints, durable bounded
  attempts and an idempotent synthetic subscription test. Delivery success does
  not acknowledge consumption or prove an agent turn.
- Private principal/key-owned feedback with bounded authorized references and no
  external delivery; matching Python/npm/REST/MCP/gateway interfaces.
- Search channel exclusion, explicit creation-time consumer start at now, calendar
  field diffs and bounded advisory overlap checks within visible calendars.

## Separate work and operational boundaries

Host onboarding links, verified idle-agent wake-up, unsolicited MCP streaming,
silence timers, quiet hours, debounce/batching, minimal response projection and
an owner-reviewed primary/duplicate calendar mapping remain separate work in the
backlog. A receiver registration is an operator action, not a URL accepted from
an agent. Real provider actions and live acceptance require their existing
authorization and synthetic tests never substitute for them.

The separate Grok voice change is [PR #28](https://github.com/GoSync-Inc/deedkit/pull/28).
No voice-engine delivery or live-call success is asserted by this review.
