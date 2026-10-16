# DeedKit JavaScript client

GoSync's dependency-free client and CLI for agents that use an existing DeedKit
gateway. Requires Node 22.14 or later. It includes ESM, TypeScript declarations
and the `deedkit` command. It calls the gateway's REST endpoint
(`POST /v1/<tool>`) for every tool in the reviewed gateway catalog: messages,
calendar and calls, plus `platform_capabilities`. It does not run a gateway,
pair messenger accounts or bridge MCP over stdio; MCP clients connect to the
gateway's `/mcp` endpoint directly.

The older `anymsg` package talks to the messaging server directly with
unprefixed operation names. It remains available for compatibility.

```js
import { DeedKitClient } from 'deedkit';
const deedkit = new DeedKitClient({
  baseUrl: 'https://gateway.example.com',
  token: process.env.DEEDKIT_GATEWAY_KEY,
});
const platform = await deedkit.platformCapabilities();
const hits = await deedkit.call('messages_search', { query: 'contract', limit: 10 });
const calendars = await deedkit.call('calendar_calendars', {});
```

`baseUrl` is the gateway's public root (or proxy prefix), without `/mcp` or `/v1`.
Use the agent's own gateway key. The client requires HTTPS except on loopback,
refuses redirects, bounds requests (64 KiB; 352 KiB for `messages_upload_file_chunk`)
and responses (16 MiB by default) and applies a 45-second deadline. It never
retries. The gateway validates arguments against its catalog schemas; the client
rejects unknown tool names, non-JSON values and a `token` field before sending.
`tools` and `toolCatalog` list the tools with their read-only flag.
The gateway decides which tools a key may use; `platform_capabilities` shows them.
Message content, file text and model output are untrusted data.

## Errors and unknown outcomes

A failed call throws `DeedKitError`. `code` is the gateway's error code, unchanged
(for example `not_found_or_forbidden`, `invalid_arguments`, `backend_unavailable`,
`backend_configuration_requires_restart`), with the HTTP `status` and bounded
diagnostics: `retryable`, `retryAfterSeconds`, `nextAction`, `requestId`,
`gatewayRequestId`, `operationSent`, `phase`, `reason`, `backendError` and
`backendStatus`. Free text from the gateway or a backend is never copied into
the error. Client failures use `transport_error`, `request_aborted`,
`redirect_refused`, `invalid_response` or `response_too_large`.

`outcome` is `rejected` or `unknown`. It is `unknown` for the gateway's
`mutation_outcome_unknown`, and for any tool that can change state when the
client got no definitive gateway answer after sending it: a timeout, a broken
connection, a redirect or an unreadable response. An unknown outcome is never
`retryable`. The action may already have happened, so do not repeat it and do
not create a replacement request ID. Check its status with the original request
ID (for example `messages_get_send_status`, `messages_get_messenger_operation`,
`calls_get_call_status`) or ask the owner. See SENDING.md in the repository.

## Download an original file

```js
await deedkit.downloadAttachment({ attachment_id: id }, './invoice.pdf');
// or, with a grant already returned by messages_get_attachment:
await deedkit.downloadFile(grant, './invoice.pdf');
```

The grant's `download_url` must be a `/v1/files/<ticket>` URL of the configured
gateway; any other URL is refused before a request. The ticket is the temporary
credential, so the gateway key is not sent with it. The client creates a new
file with mode 600 (never overwriting), checks the byte count and SHA-256 from
the grant and deletes the file if the transfer fails or does not match. Do not
log or share the ticket URL.

## Command line

```sh
npx deedkit tools
npx deedkit platform_capabilities --url https://gateway.example.com --token-file /private/agent.key
npx deedkit messages_search --url https://gateway.example.com --token-file /private/agent.key --json '{"query":"contract"}'
npx deedkit download --url https://gateway.example.com --token-file /private/agent.key \
  --json '{"attachment_id":"..."}' --output ./invoice.pdf
```

`DEEDKIT_URL` and `DEEDKIT_TOKEN_FILE` can replace `--url` and `--token-file`.
The key file must be a regular file without group or other permissions
(`chmod 600`) and not a symbolic link; never put a key in command arguments.
Results go to standard output as JSON and errors to standard error as JSON.
Exit codes: 0 success, 1 rejected, 2 usage error, 3 outcome unknown. Exit code
3 carries `"outcome":"unknown"` and a notice: do not retry the command.

Sends and other actions need exact owner confirmation by the acting agent; the
gateway and this package do not obtain it.
