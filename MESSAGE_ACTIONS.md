# Message actions and agent feedback

This plan keeps collection/retrieval separate from external actions. Existing
history, storage and file-understanding work remains in [ROADMAP.md](ROADMAP.md).
An agent report is evidence about a client/version, not permission to send or
change approval policy. Reproduce findings against the current server first.

## Immediate follow-up (0.9.12)

- Complete missing Telegram private short-send senders from the cached self
  entity after checking the configured account owner ID. Never infer anonymous
  group/channel senders or replace a known sender.
- Repair at most 100 legacy private outgoing records on collector startup,
  requiring matching confirmed outbox account, chat, provider message ID, text
  and reply. Append an archive revision, preserving original snapshots.
  Content/timestamp changes remain conflicts. Resolve only equivalent competing
  copies; unrelated contradictions remain visible.
- Expose `agent_send` in history/search/context/updates, including compact MCP
  results. Attribute the principal through a confirmed outbox receipt. Multiple
  receipts for one provider reference are explicitly ambiguous. Distinguish
  edited message text from the original sent text.
- The caller's send receipt includes its principal, credential ID and request ID.
  Other readers do not receive credential/request IDs. Provenance adds no token,
  request hash or original outgoing body.
- `owner_approval_verified_by_server` remains false: the outbox proves an agent
  action, but does not prove owner approval.

Acceptance: real Telethon message shapes, synthetic PostgreSQL fixtures,
immutable-revision and denied-scope checks, no provider sends. Production checks
must separately report which legacy records were repaired.

## Delivery and read observations (implemented in 0.9.13)

The implemented contract and coverage limits are in [RECEIPTS.md](RECEIPTS.md).

Keep provider acceptance (`sent`) separate from delivery/read/played observations.
Receipt capture must not mark provider chats read or acknowledge archive updates.

1. Add a durable, idempotent observation ledger keyed by account + provider chat
   + message reference, accepting receipts before message ingestion. Persist
   provider time when supplied and observation time separately.
2. Capture Telegram outgoing read boundaries through the existing client. The
   [outbox-read update](https://core.telegram.org/constructor/updateReadHistoryOutbox)
   supplies a peer and maximum message ID, not an exact recipient read timestamp
   or separate device-delivery timestamp. Unknown times stay null. A private-chat
   boundary must not be described as every participant reading a group message.
3. Capture WhatsApp observations through the current whatsapp-web.js
   [`message_ack` event](https://docs.wwebjs.dev/Client.html#event:message_ack) and
   [ACK states](https://docs.wwebjs.dev/global.html#MessageAck). The report's
   whatsmeow example does not describe the deployed transport. Missing read
   receipts mean unknown. Start with events, without extra authenticated sessions
   or background provider polling.
4. Expose receipt state, basis, `observed_at` and optional provider time in send
   status and message evidence. Emit deduplicated receipt updates; extend kind
   filters without silently changing existing consumer bindings.
5. Test out-of-order/duplicate events, restart/spool recovery, fencing, account
   isolation, groups, ACL changes, migrations and quarantine backup restore.
   Live acceptance needs a separately approved send and a recipient read.

## Then: reactions and expanded sends

Formatting is a new requirement from the subsequent feedback. Its staged
contract is in [FORMATTING.md](FORMATTING.md): maintained parser evaluation,
passive preview, native rendering, payload-bound outbox persistence, then incoming
format evidence. It is not implemented in 0.9.13. Plan it after receipt capture
and before expanded sends; it needs no new provider session. Long messages stay
single actions until an explicit multipart protocol exists.

| Stage | Scope | Acceptance boundary |
| --- | --- | --- |
| Read reactions | Emoji, actor/time only when supplied; changes/removals | Unknown/anonymous actors explicit; account isolation |
| Files and voice | Registered originals; no arbitrary filesystem path or URL | Exact attachment approval, MIME/size quotas, upload uncertainty |
| Edit/delete own sends | Explicit target revision and approved change | Reject stale targets; preserve evidence; no blind retries |
| Reactions/read marks | Separate external actions and permissions | Exact approval; ordinary retrieval stays passive |
| Server approvals | Optional drafts and owner approval channel | Payload-bound, single-use, expiring proof; agent keys cannot approve drafts |

Text reply targeting already exists and needs regression/live verification.
Typing indicators are lower priority. A future server approval flow must be
explicitly configured: the report does not change acting-agent approval mode.

## Client discovery

The current manifest has 19 tools. A different client count alone does not prove
caching: compare actual names, the authenticated server's `tools/list`,
`capabilities.tool_manifest` and any host filtering/configuration first. Check
the caller's effective permissions separately; discovery never grants sending
access. The existing REST fallback uses the same ACL and is useful for diagnosis
but does not refresh a client's tool cache.

The current stateless JSON POST transport explicitly rejects a notification
GET stream with 405. That is permitted by the
[MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
Do not advertise `tools.listChanged: true` without implementing notification
delivery. The [tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
ties that capability to list-change notifications; it does not guarantee that
every client discards its persistent cache.

Implement discovery notifications as a separate compatibility stage: bind streams
to authenticated principals, recheck revoked/expired keys, isolate clients,
bound queues/reconnect resources and announce only manifest changes. Test server
restart/deployment recovery and fresh `tools/list` in representative clients.
Avoid adding a GET stream that never emits data merely to hide the old timeout.
Until then, refresh/recreate the connector when stale discovery is established,
and keep unresolved host filtering/cache behavior explicit.

## Older report sections

Strict arguments, compact responses, filtered subscriptions, source diagnostics
and Ogg/Opus input already exist. They do not prove live extraction, full history
or zero client cache problems. History waves, exhausted media recovery, storage
capacity, file analysis and MCP notification transport remain outstanding.

`connected` is not proof of complete history: distinguish live capture from
catch-up gaps, archive conflicts and media download failures. Storage-paused
originals need capacity/lifecycle work, not a lower safety floor or a blind
collector restart. Classify conflicting observations with evidence; never clear
them in bulk to make an account summary look healthy. The retrieval roadmap and
action roadmap remain separate work streams, with neither declared complete.
