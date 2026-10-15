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

`platform_capabilities.tools` and a fresh MCP `tools/list` use the same effective
catalog. Both expose `discovery` metadata (under `_meta["deedkit.discovery"]` for
`tools/list`): selected tool count, a SHA-256 of the effective public schemas,
policy revision, and explicit `notifications_supported: false`. Action-enum changes
also change the hash. A host can cache its registered tools; after an authorized
profile expansion, compare a fresh list with the host registration and reconnect
when they differ. Cached tool visibility never bypasses current call authorization.
This diagnostic does not claim that a particular host has refreshed its registration.

Module capabilities preserve the backend `principal` and add `backend_principal`
and `gateway_principal`; platform `principal` remains the gateway identity.
Subscription ownership and receipts continue to use the existing backend credential.
A missing backend principal is reported as null rather than inferred from a gateway
label. Safe `backend_tool_manifest` summaries preserve the version, catalog count
and hash, and the reviewed discovery hint without advertising unselected backend
methods or a backend-specific REST URL. Platform introspection returns these as
`backend_tool_manifests` keyed by module.

`gateway_actions` always refers to the `messenger_operations` action group;
`gateway_actions_scope` makes this explicit. Inspect `gateway_tools` for calendar
and calls methods. An empty messenger action list and `owner_decision_write: false`
do not deny a selected calls preparation method. Calls channel, destination, scope
and owned-brief checks remain the backend's authority; these labels grant no access.

Backend HTTP failures retain only their numeric `backend_status`, a reviewed
`backend_error` classification (including `query_timeout`), and a UUID-shaped
`request_id` when supplied. Error bodies, exception text and transport details are
not forwarded as diagnostics. MCP and REST return the same safe error. A failure
after a mutation is dispatched still reports `mutation_outcome_unknown` and forbids
automatic retries, even when a backend error classification is available.

`messages_request_file_download` explicitly schedules bounded retrieval of one
existing archive attachment. Select it in the profile and grant `download` locally
and upstream; the backend must advertise `request_file_download` in its manifest.
It does not send messages or mark them read, and returns without waiting for bytes.
Inspect its request/download status and poll `messages_get_attachment` passively.
Repeated requests coalesce; source availability, storage limits and provider backoff
remain in force. Existing profiles do not automatically gain this tool.

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

Subscription consumption and private feedback are separately selected tools.
`messages_fetch_subscription_events` and `messages_wait_for_subscription_events`
return owned pages; only the explicit `messages_ack_subscription_events` with
`subscription-ack` can acknowledge an issued receipt. Delivery configuration and
synthetic receiver probes require `read` plus `subscription-write`; they accept an
opaque operator-registered receiver ID. A hint reaching a receiver does not prove
an agent ran a turn and does not acknowledge any event. Feedback submission requires
`read` plus `feedback-write`, stays private to the exact backend credential and
never publishes an external issue or message. Each new tool also requires an exact
backend manifest entry. Existing profiles gain none of these names automatically.

Long subscription waits and sends with positive `wait_s` use a separate pool with
2 active and 4 waiting requests; ordinary messaging reads retain their own pool.
Both waits are bounded to 25 seconds. Cancellation releases waiting capacity.
`messages_get_updates(start_from="now")` requires `cursor-write` both locally and
upstream, initializes only a new consumer, and cannot reset an existing consumer's
ACK state. The legacy default remains `start_from="beginning"`.

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
