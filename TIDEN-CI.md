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
- Variable `TIDEN_BRANCH_MAP`: a JSON object mapping Git branches to existing
  Tiden spec branches, for example `{"codex/example":"intent/example"}`.
  `main` maps to Tiden `main` automatically.

The mapping must name an existing Tiden branch. Do not label a feature run as
`main` just to make the main gate green. Missing mappings and failed evidence
association fail the reporting job. Without a token the job clearly states
that reporting is not configured. No workspace credentials belong in Git.

For an explicit baseline rerun, dispatch `CI` on the reporting-code branch with
`source_ref` set to the exact source revision and `tiden_branch` to the matching
existing spec branch. The checkout SHA, rather than the workflow SHA, is recorded.
Normal pull requests record their actual tested merge SHA.

## Verdicts and coverage

`run create` always receives `--branch <Tiden branch> --intent-session none`:
do not rely on inferred Git-to-intent routing. The job waits for live-doc sync
and checks the resulting branch ID before reporting success. The authentic run
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
intent or roadmap; do not confuse `Backlog` with missing test coverage or set
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
