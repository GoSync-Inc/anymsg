# DeedKit gateway

A single MCP/REST boundary over independent domain services. This package contains
no collectors, databases, provider credentials, calendar or voice libraries.

The owner supplies a private JSON policy file (`0600`), explicit deployment URLs,
exact profile tool names, principal key hashes, scopes and separately stored backend
keys with pinned hashes. Modules and methods are never discovered by importing code
or automatically exposed when a backend adds a feature. Missing backend capability
contracts fail closed. The calendar backend must provide scoped `permissions`.

```sh
uv sync --locked
uv run deedkit-gateway --policy /private/policy.json --public-url https://agents.example --check
uv run deedkit-gateway --policy /private/policy.json --public-url https://agents.example
```

Default binding is loopback port 13240. Deploy behind an authenticated TLS network
boundary with the exact configured Host; forwarded identity headers are not trusted.
The policy is validated at startup and before every admission. Replace it atomically,
increase `revision`, and keep the same principal/backend key binding when rotating
only a gateway key. Backend URL changes require a restart. Existing subscription
owners are backend credential identities, not gateway session IDs.

`POST /mcp` uses the official SDK transport and lifespan. `GET /mcp` returns 405;
notifications/SSE are disabled. `POST /v1/{selected_tool}` uses the same authorization
and argument schema. There is no generic arbitrary backend executor. Unknown writes
are not retried. Capability discovery intersects profile, local grants and current
backend permissions. An unavailable module leaves platform introspection available.

Messenger-operation enums also intersect the backend's explicit supported names
for each permitted account. A mixed Telegram/WhatsApp key sees their union; each
preview/execute rechecks the exact account before dispatch. Unsupported operations
are omitted from account catalogs. Missing support names hide live actions rather
than inferring them from a provider name or count. Scoped receipt reads remain
available across provider changes under the existing action permission ceiling.
Upgrade the messaging backend before the gateway when adding this contract.

New file staging is separately selected with `messages_prepare_file_upload`,
`messages_upload_file_chunk` and `messages_get_file_upload`. It requires current
backend manifest support, local and backend `read`, `send`, `upload` grants and
an allowed `send_file` action. The current `send_file` ceiling also requires
`download`, including sends using newly uploaded files; this draft preserves that
existing authorization model. Existing profiles do not gain upload access.
Source bytes are staged for one credential/account/chat; URLs and server paths
are not accepted. Uploading never sends to the messenger or asserts owner approval.
Chunks contain at most 262144 decoded bytes, 349528 base64 characters, and an
exact 360448-byte HTTP/MCP envelope cap. Every other gateway request keeps the
65536-byte cap. A separate upload pool admits 2 active and 4 waiting requests,
with 8 chunks per second per backend credential. Body reads and upstream chunk
transfers each have a 10-second deadline.
Read back the upload's next offset after an uncertain response; writes are not retried.

Original files are streamed through bounded, short-lived bearer tickets with separate
connection pools. URLs cannot select another origin or path. Tickets recheck current
local policy and credential binding, and poll the backend's existing HEAD download
grant check during long transfers. Revocation terminates an active response; a partial
file is never converted to a successful error JSON. Clients should verify the advertised
length and SHA-256. Do not share or log these bearer URLs. Access logging is disabled.

Deployment profiles in `config/profiles/` select managed or external backends. Run
`python scripts/deedkit.py resolve --profile config/profiles/existing-readers.toml --json`
from the repository root to inspect a plan without starting a process. Resolution is
not a health check or proof that the private policy grants the listed profile tools.

Normal tests use synthetic HTTP doubles and real SDK loopback transport. They do not
read accounts or start provider sessions. Full acceptance also requires real isolated
backend tests, load budgets, server restore evidence and Tiden readback; see
`docs/quality/platform-acceptance.md` at the repository root.
