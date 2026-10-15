# AnyMsg JavaScript client

GoSync's dependency-free HTTP client for an existing AnyMsg messenger archive.
Requires Node 22.14 or later. This package includes ESM, TypeScript declarations
and a CLI. It does not create a server or authenticate Telegram/WhatsApp accounts.

```js
import { AnyMsgClient } from 'anymsg';
const archive = new AnyMsgClient({
  baseUrl: 'https://messages.example.com',
  token: process.env.ANYMSG_TOKEN,
});
const hits = await archive.search({ query: 'contract', limit: 10 });
const status = await archive.getSyncStatus();
```

`baseUrl` is the server root (or proxy prefix), without `/mcp` or `/v1`.
Every agent must use its own scoped credential. The client requires HTTPS except
on loopback, refuses redirects, bounds requests/responses and applies a 30-second
timeout through response consumption. It never retries acknowledgements or reviews.
`AnyMsgError` provides `code`, `status`, `requestId` and `retryable` for diagnostics.
Message content and model output are untrusted data. Check coverage in results.

```sh
npx anymsg capabilities --url https://messages.example.com --token-file /private/agent.key
npx anymsg search --url https://messages.example.com --token-file /private/agent.key --json '{"query":"contract"}'
```

Keep token files private (`chmod 600`); do not put tokens in command arguments.
Use `call('operation', arguments)` for the allowlisted HTTP operations exported as `tools`.
Confirmed text sending uses `sendMessage` and `getSendStatus`, separate send rights
and account opt-in. The acting agent must obtain exact owner confirmation; the
server has no approval gate yet. Reuse the same action ID after a lost response;
never automatically resend an unknown result. MCP clients connect directly to the
server's `/mcp` endpoint; this package is not a stdio MCP bridge.

Server, source and Python package: https://github.com/GoSync-Inc/deedkit.
MIT license, copyright GoSync.

Development source 0.9.17 adds `dismissReply({chat_id, message_id,
expected_revision})`. Read the current `listChats({awaiting_reply:true})` hint
first; dismissal affects only this credential and needs read + cursor-write.
`reply_target_changed` means refresh the hint, not automatically retry a changed
write. Queue filters and priority use the same snake_case parameters as REST.
See the server's SEARCH.md for owner-read observations and heuristic limits.

## Stage a file or photo without sending it

Development source supports `prepareFileUpload`, `uploadFileChunk` and
`getFileUpload`, plus the local-file helper `uploadFile`. Uploads are separate
from messenger actions and scoped to one agent key, account and exact archive
chat. They require explicit `read`, `send` and `upload` grants, account sending
opt-in and the account's `send_file` action policy. The gateway currently also
requires its existing `download` ceiling for `send_file`; upload permission alone
does not broaden that policy.

```js
const staged = await archive.uploadFile('/local/photo.jpg', {
  account: 'whatsapp-primary',
  chat_id: 'chat_resolved_with_this_account',
  filename: 'photo.jpg',
  mime: 'image/jpeg',
  request_id: 'photo-upload-2026-001',
});
// staged.upload_id + staged.sha256 identify ready bytes. Nothing was sent.
```

The helper streams SHA-256 and 256 KiB chunks from one open descriptor, rechecks
the original and rejects source changes. It accepts regular files of 1 byte to
32 MiB. Symlinks and platforms without `O_NOFOLLOW` are rejected. `filename`
defaults to the basename and `mime` to `application/octet-stream`; local paths
and URLs never enter the HTTP protocol. Chunks are paced to fewer than 8/sec for
one helper; concurrent helpers sharing a credential may still hit its rate limit.
Only `upload_file_chunk` can use a 360448-byte JSON body. Other SDK requests keep
their 16 KiB limit. Pass an `AbortSignal` through the helper's `signal` option to
cancel locally; partial uploads expire on the server.

```sh
npx anymsg upload_file --file /local/photo.jpg --url https://messages.example.com --token-file /private/agent.key --json '{"account":"whatsapp-primary","chat_id":"chat_resolved_with_this_account","mime":"image/jpeg","request_id":"photo-upload-2026-001"}'
```

After an uncertain response the SDK stops without retrying or inventing an ID.
Inspect `getFileUpload({upload_id})` if its ID is known. Repeating the helper
explicitly with the same `request_id`, source bytes and metadata can reconcile
preparation and resume from the server's exact offset. Do not use a fresh ID to
hide an unknown outcome. Source changes or an invalid receipt also stop the
helper; staging may already exist, but no messenger send is attempted.

Sending remains separate: review `previewMessengerOperation` with operation
`send_file`, exact `account`/`chat_id` and parameters
`{upload_id: staged.upload_id, sha256: staged.sha256, mode: 'photo'}` (or
`mode: 'document'`). Only after exact owner confirmation execute that same
reviewed payload with its preview digest and a stable action `request_id`.
Never provide both `attachment_id` and `upload_id`.

The Python SDK offers the same contract:

```python
staged = client.upload_file('/local/photo.jpg', account='whatsapp-primary',
    chat_id='chat_resolved_with_this_account', filename='photo.jpg',
    mime='image/jpeg', request_id='photo-upload-2026-001')
# Low-level: prepare_file_upload(...), upload_file_chunk(...), get_file_upload(...).
```

The Python helper is synchronous and closes its descriptor on completion or
failure. It does not fetch URLs, copy originals back from the server or send
messages. This upload support is a source release candidate until published.
