# DeedKit module runtime

The platform lives in the existing repository. Gateway, calendar and calls have
separate Python environments and locks. Messaging retains its package/import/CLI
names, schema and original test identities. `config/stack.toml` checks the shared
resolved versions without installing a union of provider dependencies.

## Selecting modules

Run `python scripts/deedkit.py resolve --profile config/profiles/calendar-reader.toml --json`
to inspect the deployment. `up`, `status` and `stop` use explicit module Compose
projects and services. Stop never removes volumes. Mark an existing backend as
`external` to use its configured endpoint without starting another database or
provider session. The phone-only profile does not require messaging. Telegram calls
use the existing session owner's broker through the optional calls-telegram overlay.

The launcher plan is deployment selection. The private gateway policy is the
authority for exposure: its exact profile tool names, principal grants and backend
permissions must all allow an operation. An empty profile stays empty. A disabled
or unavailable backend never silently falls back to another account or provider.

For generic messenger operations, both the profile and principal must explicitly
list actions under `messenger_operations`. Their intersection is further restricted
by current backend permissions. `rename_group` does not allow `leave_group` even
though both use the backend `groups-manage` permission. MCP schemas and operation
discovery show the same ceiling; REST applies it too. Backend account opt-in and
payload-bound preview digests remain authoritative at execution.

## Persistent state

Calendar requires an explicit existing data directory containing role-specific
configuration. Only its worker mounts the credential vault. The API and gateway
do not receive provider credentials. A PostgreSQL session advisory lock rejects a
second worker before job recovery or provider access. Calendar DB volumes are
explicit external volumes, so an accidental project rename cannot silently create
an empty replacement database.

Calls uses `VOICE_DATA_DIR` (or the stable user-state path under
`~/.local/share/deedkit/calls/data`), never a path computed from source checkout
parents. Initialize a new private directory explicitly before starting; migrations
must point to the existing state. The Compose bind refuses to create a missing
directory. Old browser/owner tasks retain their legacy boundary.

Calls agent API credentials are separate from the owner control token. An atomic
private `VOICE_AGENT_REGISTRY` JSON file contains version 1, a monotonically
increasing revision and principals with ID, SHA-256 key digest, enabled flag,
timezone-aware expiry, scopes, channels and exact destination allowlists. Brief
ownership is assigned from the authenticated principal before snapshot hashing.
Calls, results, decisions and followups inherit it. No agent tool can submit an
owner decision or read/activate global learned prompts. Optional archive context
uses that principal's separate private archive configuration; it never falls back
to the owner archive credential. Phone preparation works without an archive.

## Outcomes and verification

Preview, plan and brief preparation do not authorize real external actions. The
acting agent still needs the owner's concrete instruction. Domain request IDs,
digests, receipts, expected revisions and unknown outcomes pass through unchanged.
A transport failure after a mutation dispatch is explicitly reported as unknown;
the gateway does not resend it or invent a new request ID.

Original files use bounded, short-lived gateway bearer links. SHA/length are checked
while streaming. Gateway revocation is rechecked every chunk and by a 250 ms watcher;
backend access is revalidated with the authenticated backend grant's HEAD route at
one-second intervals with a two-second timeout. Failure aborts the stream, including
a stalled client. Restart invalidates temporary links. Never log download paths,
credentials, source content or private filenames at the reverse proxy.

CI retains the existing messaging checks and adds module JUnit reports with qualified
source identities. Cross-module tests use real HTTP and isolated synthetic databases.
The load runner saves raw timings for direct REST, gateway REST and MCP, at least
1,000 requests per scenario and three repeats, both isolated and contended. These
measurements concern the gateway and synthetic backends, not provider latency or
successful live calls. Runtime cutover and provider readback require their separate
migration receipts.
