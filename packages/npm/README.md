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
Use `call('operation', arguments)` for any of the 17 current HTTP operations.
Confirmed text sending uses `sendMessage` and `getSendStatus`, separate send rights
and account opt-in. The acting agent must obtain exact owner confirmation; the
server has no approval gate yet. Reuse the same action ID after a lost response;
never automatically resend an unknown result. MCP clients connect directly to the
server's `/mcp` endpoint; this package is not a stdio MCP bridge.

Server, source and Python package: https://github.com/GoSync-Inc/anymsg.
MIT license, copyright GoSync.
