# Verified production promotion

Production changes follow PR → required CI → merge into `main` → CI on the
actual main SHA → prepare → explicit apply. Use `scripts/production.py` for
promotion. Direct source copies, ad hoc image overrides and `compose up --build`
skip this contract. Root access can bypass deployment policy; keep that access
for recovery and route routine changes through this tool.

## Required CI and main protection

`CI gate` runs even when dependencies fail or skip and accepts only success from
the shared package/module checks, native Go race tests/vet, and all seven
production image builds with isolated imports. The existing shared checks cover
synthetic PostgreSQL, Python/Node, installed distributions, release inventories,
gateway/calendar/calls and load budgets. Tiden reports actual outcomes separately.
No test receives a provider session or production database.

Protect `main` with required GitHub Actions `CI gate` and `checks / verify`,
strict up-to-date branches, PR-only merges, administrator enforcement, conversation
resolution, and disabled force pushes/deletion. A single maintainer may use zero
required approving reviews; this still requires a PR and green checks. Native
verification is reusable and unconditional in CI, so path filters cannot hide it.
The reviewed desired settings are in `deploy/main-protection.json`. An
administrator can apply them with
`gh api --method PUT repos/GoSync-Inc/deedkit/branches/main/protection --input deploy/main-protection.json`
and read back that endpoint to verify the actual settings.

## Host setup

Keep administration behind the existing Tailscale/OpenSSH route. CI needs no SSH
key, tailnet enrollment, provider token or production database access.

Install a reviewed copy of `scripts/production.py` as
`/usr/local/lib/deedkit/production.py`, owned by root and writable only by root.
Use Python 3.12+, Git, Docker and Compose v2 on the Linux host. Do not execute
the deployment tool from an unreviewed feature branch on the host.

Copy `deploy/production.example.json` to `/etc/deedkit/production.json` and
adapt it to the existing host. The operator owns this file; mode `0600` is
recommended. It lists **all** existing Compose files in their exact order,
explicit environment files, the current working directory/project name and only
the currently running application services to replace. Obtain those paths from
`com.docker.compose.project.*` container labels, including existing private
overlays. Do not replace the host's Compose topology with the example.

Available build targets are `runtime` (API, Telegram, subscriptions), `indexer`,
`enricher-local`, `whatsapp-native`, `gateway`, `calendar` and `calls`. The two
calendar services share the `calendar` image; a legacy service named `voice`
can select `calls`. Explicitly select only the deployed native WhatsApp adapter;
this tool does not convert browser sessions. Databases and unrelated services
are never selected. State, mounts, networks, ports and private settings are
retained from the resolved configuration.
All profiles are included when resolving the snapshot so existing profiled
collectors/workers remain visible. Apply still names only the explicitly selected
services and uses `--no-deps`; loading profiles does not launch other services.

Before initial enrollment, compare the resolved configuration with the running
containers and reconcile missing shell variables or settings drift. Container
labels list Compose paths but do not capture shell environment overrides. The
prepared snapshot preserves the resolved files; an explicit candidate review
must confirm that they are the intended production settings.

Host-mounted code and import-path overrides are refused, including legacy
Telegram bridge files mounted outside the image. Move such extensions into the
reviewed source/image and test them in CI before selecting that service. The
tool accepts mounts under the established data/config roots (`/data`,
`/sessions`, `/spool`, `/run/secrets`, `/run/deedkit`, `/models`,
`/processing-locks`, `/broker`, `/voice-broker`); it refuses executable file
mounts and other roots. It never silently drops a legacy extension.

The messaging `runtime` image includes the existing `telegram_session_owner` and
`telegram_broker` modules. For an existing broker-enabled collector, use
`python -m telegram_session_owner --config /run/secrets/telegram_config` from the
image, preserving its broker socket/token/policy configuration and session/data
mounts. Remove only the replaced Python file mounts and `PYTHONPATH` override
from the reviewed candidate; keep the default collector command for installations
without this extension. CI and preparation import the modules without connecting
an account or starting a broker.

The state directory and Docker filesystem each need at least **15 GiB free**
before preparation. A refusal does not authorize pruning shared Docker objects
or deleting runtime data. Free capacity through a reviewed, targeted operation
or add storage before retrying.

## Prepare and inspect

```sh
python3 /usr/local/lib/deedkit/production.py verify --sha <40-character-main-sha>
python3 /usr/local/lib/deedkit/production.py prepare --sha <40-character-main-sha>
```

The guard queries GitHub directly. Only the latest successful **push** CI run
for the exact current `main` SHA qualifies, with successful package, native and
aggregate jobs from its current attempt. A PR run, manual baseline run, earlier
successful run followed by failure, missing job or skipped check is refused.
The public repository supports anonymous API reads; optional `GH_TOKEN` may be
provided by the operator's secret mechanism to increase API limits. Never copy a
maintainer's broad token onto the host for routine deployment.

Preparation fetches only `main`, checks the fetched SHA again, builds the
selected targets from that clean detached checkout and smoke-tests images with
no network, sessions, mounts or database. Source downloads and image builds do
not stop or replace running services. Images carry the exact source SHA and the
prepared Compose snapshots pin Docker image IDs. Inspect `prepared.json`, the
image inventory and the private `*.before.json`/`*.json` Compose diff under
`<state_directory>/<sha>/`. Full snapshots can contain secrets: keep them on the
host with mode `0600`, outside Git, packages and CI artifacts. Public receipts
contain only revision, CI URL and image IDs.
The previous snapshot pins each selected container's actual image ID, including
when the old configuration used a mutable tag; it does not depend on the tag
continuing to reference the previous image.

Before applying, follow [backup/migration and rollback](platform-cutover.md).
This tool does not run migrations, prove data compatibility, authorize external
actions, or perform provider readback. A schema change needs the module's explicit
migration procedure and compatible backup first. Preparation is not that approval.

## Apply and verify

```sh
python3 /usr/local/lib/deedkit/production.py apply --sha <same-sha>
python3 /usr/local/lib/deedkit/production.py status
```

Apply locks the host, checks current main/CI again and validates the prepared
image labels, configuration hashes and existing container identities. If another
operator changed a runtime or config after preparation, prepare a fresh reviewed
candidate. The selected services stop before their replacements start; `up` uses
`--no-build --pull never --no-deps --wait`. Databases and other Compose projects
are not restarted. Routine promotion may briefly interrupt selected services.

`current.json` is written only after every selected replacement runs its prepared
image. It records image/start verification; it is not a claim that every upstream
provider or client is healthy. Use each module's approved health/readback procedure.
No test send, ACK, call, invitation or new provider session is a health probe.

An interrupted/failed apply leaves `attempted.json`, retains previous image IDs
and refuses blind retries. A multi-project failure can leave some projects
updated. Inspect actual container/image state before recovery. Never assume an
automatic rollback is data-safe; restore compatibility/backup as the module
runbook requires, stop the new session owner, then restore the recorded previous
image/config. Do not use `down -v`, prune unrelated objects or restart a stale
calendar writer. Keep the previous images until acceptance.

There is deliberately no unattended restart timer. Only `apply` changes runtime
processes, after review of the candidate and any required live-session authority.

GitHub documents [required checks and administrator enforcement](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
and [failure/skip handling for dependent jobs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs).
