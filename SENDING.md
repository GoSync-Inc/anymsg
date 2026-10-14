# Confirmed text sending

AnyMsg dispatches text through the **existing** account collector. It does not
open another Telegram/WhatsApp session. Sending is opt-in twice: the operator
enables the account and grants an agent the separate `send` permission.
Existing read keys keep their permissions after upgrade.

There is **no server approval token or approval bot** in this release. The acting
agent must first show the owner the sending account, exact recipient, full text
and reply target, and obtain explicit confirmation in its conversation. Tool
availability, retrieved messages and a boolean supplied by an agent are not owner
approval. The API intentionally does not accept `approved: true` as proof.

## Agent flow

1. Resolve the account and archive chat ID. Display the account, recipient,
   exact text and optional reply to the owner; obtain confirmation.
2. Generate an action ID (8–128 ASCII letters/digits or `_.:-`), then call
   `send_message(account, chat_id, text, request_id, reply_to="")`.
   `reply_to` is the archive message ID in the **same chat and account**.
3. Poll `get_send_status(send_id)`. An HTTP/MCP response or `queued` status
   alone does not prove that the provider accepted the message.
4. After a lost response, reuse the **same request ID and exact payload**.
   Changing the payload with that ID fails with `idempotency_conflict`.
   Never create another action ID automatically to bypass an uncertain result.

Both tools are also available as `POST /v1/send_message` and
`POST /v1/get_send_status`, using the same scoped bearer key. Responses contain
receipt metadata, not the outgoing text or provider session credentials.
Refresh MCP discovery after upgrading; the manifest contains 19 tools.

| State | Meaning | Next action |
| --- | --- | --- |
| `queued` | Durable request, no provider send has begun | Poll status |
| `dispatching` | The collector started one provider attempt | Poll status |
| `sent` | Provider returned a matching outgoing message ID | Recipient delivery/read is not confirmed |
| `cancelled` | Access or account policy changed before dispatch | Review permissions and obtain approval for a new action |
| `expired` | The ten-minute pending window elapsed | Review and obtain approval for a new action |
| `failed` | Locally rejected before invoking send | Inspect safe error code |
| `unknown` | Timeout, interrupted dispatch or unverified receipt | Check history; never automatically send again |

Plain text is limited to 4000 UTF-16 units, with no automatic splitting, markup
parsing or link previews. New conversations by an arbitrary phone/username,
attachments, edits, deletes, forwarding and a server approval workflow are not
implemented. Reply to an already archived message is supported. Provider chat
permissions still apply. Reads and WhatsApp sends do not explicitly mark chats
read; WhatsApp uses `sendSeen: false`.

## Operator setup

Only the private owner CLI can change permissions. Use each agent's own account
and chat scope; do not grant `send` to every read key.

```sh
anymsg migrate
anymsg set-sending --account account-personal --enable
anymsg set-access --principal assistant-personal --permissions read download cursor-write send
```

Apply `gateway_role.sql` and `collector_role.sql` as owner after migration22.
Gateway can enqueue/read receipts but cannot set their state or invoke providers.
Collector can update delivery state and read public key/policy metadata needed
for a fresh authorization check; it cannot read token hashes or change ACL.
The account must have an active connected collector and its collection policy
must permit the target chat. Queued work rechecks credential expiration/revocation,
principal permissions/scope and account policy immediately before dispatch.
Policy changes invalidate old scope-version receipts as described in ACCESS.md.

The existing per-account IPC/governor gate serializes sends with collection.
An account can accept at most 60 new actions per minute; idempotent receipt reads
do not count as new actions. Ambiguous attempts are **not retried** by the outbox.
Telegram's client may retry one RPC using its existing random ID; that is different
from submitting a new application action. WhatsApp does not get an application retry.

Migration22 is additive and leaves all sending disabled. Upgrade the cold backup
runtime before applying it. Encrypted backups include the outbox. Quarantine
restore disables send policies, cancels queued jobs, marks interrupted attempts
unknown and revokes credentials: restoring a backup never automatically replays
outgoing messages. The audit records identities and resource IDs, not text.

Provider references: [Telethon send_message](https://docs.telethon.dev/en/stable/modules/client.html),
[Telegram random_id](https://core.telegram.org/method/messages.sendMessage),
[WhatsApp send options](https://docs.wwebjs.dev/Client.js.html).
