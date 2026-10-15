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

Original files are streamed through bounded, short-lived bearer tickets with separate
connection pools. URLs cannot select another origin or path. Tickets recheck current
local policy and credential binding, and poll the backend's existing HEAD download
grant check during long transfers. Revocation terminates an active response; a partial
file is never converted to a successful error JSON. Clients should verify the advertised
length and SHA-256. Do not share or log these bearer URLs. Access logging is disabled.

Deployment profiles in `config/profiles/` select managed or external backends. Run
`python scripts/platform.py resolve --profile config/profiles/existing-readers.toml --json`
from the repository root to inspect a plan without starting a process. Resolution is
not a health check or proof that the private policy grants the listed profile tools.

Normal tests use synthetic HTTP doubles and real SDK loopback transport. They do not
read accounts or start provider sessions. Full acceptance also requires real isolated
backend tests, load budgets, server restore evidence and Tiden readback; see
`docs/quality/platform-acceptance.md` at the repository root.
