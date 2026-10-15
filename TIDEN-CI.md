# Tiden verification evidence

CI keeps the original Python and Node JUnit reports, their normalized copies,
the exact **tested checkout SHA**, step outcomes and the GitHub Actions URL in
the `ci-evidence` artifact, including failed runs. Python uses xunit1 to retain
source paths; Node's generic `test` classname is qualified by its source file.
Parameterized Python executions retain their parameter names and status.

The separate reporting job receives no database or messenger credentials. It
uses Tiden CLI 0.33.0 from the official release with a pinned SHA-256 checksum.
Fork pull requests do not receive the Tiden secret or publish evidence. Package
release workflows that do not pass this optional secret still verify packages.

## Configuration

In GitHub Actions repository settings, add:

- Secret `TIDEN_API_TOKEN`: a Tiden workspace API credential.
- Variables `TIDEN_WORKSPACE_ID` and `TIDEN_PRODUCT_ID` for the linked product.
- Optional variable `TIDEN_BRANCH_MAP`: a JSON object mapping Git branches to
  existing Tiden spec branches, for example `{"codex/example":"intent/example"}`.
  `main` maps to Tiden `main` automatically.

A mapping must name an existing Tiden branch and always wins. A feature branch
without a mapping is reported under its own Git branch name. Tiden skips
live-doc sync for a branch name it does not know, so the publisher first creates
a Tiden branch of that name when none exists. Tiden may instead attribute the run
to the intent branch whose session (`tiden intent start` on that Git branch)
recorded it. The publisher reads the result back and accepts only that
same-named branch or an `intent/` branch, never `main`. Git branch names Tiden
cannot use (uppercase, dots) still need a mapping. Do not label a feature run as
`main` just to make the main gate green. Failed evidence association fails the reporting job. Without a token the
job clearly states that reporting is not configured. No workspace credentials
belong in Git.

For an explicit baseline rerun, dispatch `CI` on the reporting-code branch with
`source_ref` set to the exact source revision and `tiden_branch` to the matching
existing spec branch. The checkout SHA, rather than the workflow SHA, is recorded.
Normal pull requests record their actual tested merge SHA.

## Verdicts and coverage

`run create` always receives `--branch` and `--intent-session none`. A mapped
run must land on exactly the mapped branch ID. An unmapped run must land on the
same-named or an intent branch, as described above. The job waits for live-doc
sync and checks the resulting branch before reporting success. The authentic run
then adopts existing case identities or imports the reported cases. Reported
step failures and missing required verifications are also recorded as failed
or blocked; optional test skips remain skipped. A test run is not a statement
that every specification is covered.

Link specs only to tests whose assertions actually verify them. Use
`tiden test link-spec <test-id> --spec-id <spec-id> --branch <branch>` after
reviewing the body. File proximity alone is not sufficient. CI does not use
`test ingest` or rewrite spec links, so future runs preserve existing links.
The `tiden-receipt` artifact records the run, association and verdict.

## Maintaining the product map

[ARCHITECTURE.md](ARCHITECTURE.md) defines the logical component boundaries.
They differ from distribution packages and running services. Preserve existing
spec/test identities when changing component assignments, and retain historical
source anchors alongside the reviewed revision.

Reverse-engineered specs are candidates for review. Check API arguments, state
semantics, schema versions and deployment exceptions against the implementation;
a source-file anchor does not guarantee that every sentence is accurate.
Implemented baseline contracts use `Active`. New work uses an explicit change
intent or [BACKLOG.md](BACKLOG.md); do not confuse `Backlog` with missing test coverage or set
`Done` merely because a related test passes.

Review spec and test-link changes in the matching Tiden branch before merging
them to main. A Git merge does not by itself merge the spec branch. After both
merges, verify the actual main gate and its evidence, rather than using a feature
branch's projection as the main result. Existing test links survive ordinary CI
reports; authoritative imports must not silently replace them.

`no_test` means no accepted linked test; `not_run` means linked evidence has not
met the gate's run/freshness requirements. Neither label alone proves a runtime
bug. Passing synthetic tests also do not prove live-provider history coverage or
production infrastructure health. Keep those limits visible.

The original synthetic reports can contain failure diagnostics. Keep artifact
access and retention appropriate for the repository; never test against live
messenger sessions or personal archives.

## DeedKit module migration

The migration extends this same repository and product. Its behavior/proof
matrix and intent-to-PR-to-CI procedure are in
[platform acceptance](docs/quality/platform-acceptance.md). Local worktrees and
uncommitted files are not GitHub App sync inputs. Link the migration PR to its
intent branch and publish real results there before reviewing the merge. After
merge, verify both main's gate and the GitHub App processing/drift state.

The integrated CI includes gateway, calendar, calls and calls-frontend reports,
plus the measured gateway load gate. Existing messaging check/test identities
and links remain intact. Module assertions support reviewed contracts in the
same product; a successful messaging run alone cannot verify another module.

## Native and adapter proofs

The host CI supplies its disposable PostgreSQL service to the native backup
drill and installs `age`. The drill uses real `pg_dump`, encryption, the local
receiver, decryption, `pg_restore` and quarantine checks. Missing prerequisites
fail CI; a local test container without them explicitly skips these checks.
This proves local recovery behavior, not remote SSH delivery or off-site quota.

Provider tests use synthetic Telegram clients and an offline loader around the
actual WhatsApp entrypoint. They verify capture, identity checks, command lanes
and recovery states without opening an account. Local model tests verify adapter
arguments and result handling; they do not measure transcription accuracy.
Review broad contracts against the combined criterion set from their adapters,
database roles and public interfaces, rather than a single child test.
