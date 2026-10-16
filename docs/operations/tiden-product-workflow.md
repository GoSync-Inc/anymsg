# Product work through Tiden

Tiden is DeedKit's primary record of product requirements, future work, delivery
state and verification evidence. `BACKLOG.md` is a navigation entry point and
legacy-ID index. Do not maintain another editable queue in module documents,
plans, PR descriptions or chat summaries.

Use the existing [DeedKit product](https://app.tiden.ai/1dc18e09-4a09-4d74-8300-cd4302622357/products/75bd0580-2af4-4d5a-b236-c5a9d7ff7ce1).
Workspace: `1dc18e09-4a09-4d74-8300-cd4302622357`.
Product: `75bd0580-2af4-4d5a-b236-c5a9d7ff7ce1`.
Do not create a replacement product, reimport existing tests, or reset the drift
watermark to make a dashboard green.

## Standard skills first

Use the skills shipped with the installed Tiden CLI. A separate project-management
skill is unnecessary: this document supplies the project decisions those skills
cannot infer. Do not copy or fork the vendor skill bodies into another workflow.

| Skill | Use it for |
| --- | --- |
| `product-context` | Find existing specs and source anchors before planning or investigating; keep a mapping for each independent part. |
| `start-intent` | Begin or continue a scoped product change with observable promises; classify docs-only/spec-maintenance work using its skip rule. |
| `capture-intent` | Reconcile the actual product change into existing specs or new narrowly scoped stories on the session branch, then close with the measured ledger verdict. |
| `link-coverage` | Connect already reported tests to requirements after reviewing the assertions; preserve existing links. |
| `generate-tests` | Reconcile genuinely missing test cases. For this existing product, review the import plan and identity/link preservation before any authoritative ingest. |
| `triage-issues` | Investigate reported runtime errors and prove a scoped repair; issue closure is separate from deployment acceptance. |

Inspect both scopes with `tiden skill list --agent codex --global` and `--local`.
A new worktree has no local copy, so install missing vendor skills with
`tiden skill install --agent codex --global`; use `--agent claude-code` for that
agent. Do not overwrite customized skills or change global hook trust,
credentials, sandbox permissions or other projects as part of routine setup.
`tiden doctor` can report global-scope warnings even when local skills and the
current session's network access work. Check each warning's actual scope instead
of rerunning setup blindly.

The local coding-agent intent loop uses the current agent and the Tiden CLI.
Tiden's hosted Agents page is a separate automation surface; configuring it or
adding an LLM credential is not a prerequisite for this workflow.

## Requirements, work and evidence

An Epic describes a product outcome; Features group coherent capabilities;
stories describe independently reviewable behavior with acceptance criteria.
Use existing parents/components and stable identities. Keep operational acceptance
separate when merged source cannot establish the property of a real installation.

Every queued item must record:

- Desired behavior and concrete acceptance criteria.
- The boundary already implemented and the remaining work.
- Priority, dependencies, and whether the item is committed, conditional or a
  decision requiring triage.
- Historical `DK-*` identity, related specs, source/PR links and an as-of revision.
- Evidence needed to close it, including any separately authorized live action.

`High`, `Medium`, `Low` retain the old P1, P2, P3 ordering. Optional work stays
explicitly conditional; importing it does not authorize provider mutations,
publishing packages or production changes. `Critical` requires a concrete new
incident or owner priority decision.

Workflow status, merged source, test coverage and deployment are separate facts.
Use `Backlog` for future/conditional work, `Active` for work being implemented,
`Review` for a proposed implementation or acceptance awaiting review, and `Done`
only for the stated delivered scope. A broad maintained capability may remain
`Active` while its individual increments are delivered. A status update must
not weaken a contract, hide a failing test or remove it from verification scope.
Record the exact evidence and the unresolved boundary in the spec body.

The gate observed on 2026-10-16 includes queued `Backlog`/`Review` specs and the
General navigation index in its coverage calculation. These produce `no_test`
and a branch `NOT VERIFIED` verdict; main reports `BLOCKED` for the same coverage
gaps even when existing implementation evidence passes.
Workflow status does not automatically select release scope. Read the per-spec
reasons; do not create placeholder tests, link unrelated passing tests, or change
statuses to suppress this result. Release-readiness claims require a reviewed
release scope and actual evidence, separately from this whole-product plan.

An old `Active` baseline is not evidence of a currently running task. Do not
mass-convert legacy statuses without reviewing both implementation and how the
server includes that status in traceability/gates.

## Working loop

### Current truth and future proposals

Put **new future-only** requirements on a separate proposal branch per independently
deliverable increment. Record priority, dependencies and historical IDs there.
Keep the proposal open until its behavior and evidence are reviewed. Do not merge
an entire planning collection into main merely to register its existence: a branch
merge admits all of its reviewed changes, not one selected task.

Main contains maintained behavior and its known deficiencies. Existing defects,
unverified operational acceptance and unresolved production incidents remain
readiness concerns. A mixed item needs an explicit current-defect/operational-risk
boundary and a separate future-increment boundary before any relocation. A new
filter, output field or contract change is not automatically a regression.

The initial backlog import already placed future requirements on main. In the
CLI 0.33.0 and UI inspected on 2026-10-16, no supported main-to-proposal relocation
preserving the same spec identity/history was found. `spec update --branch` makes
a copy-on-write proposal; it does not remove the original from main. Creating a
release does not provide a documented spec-status filter. Until an identity-safe
relocation is verified, keep those records and report the scope debt explicitly.
Do not substitute delete/recreate, copied cards, `Done`, fabricated links or risk
acceptance for a move. A private migration manifest is an audit artifact, not a
second editable backlog.

### Readiness decision

Record these independently, with timestamp, branch/release ID and exact Git SHA:

1. Native Tiden main/branch gate and its actual reasons. A feature subtree check
   is only a subtree result; a cached verdict keeps its original computed time.
2. A captured gate for the candidate release/environment and matching CI evidence.
   A missing release gate is unknown, not a pass. A package version shared by
   several commits cannot establish the deployed SHA.
3. Unresolved production issues and component attribution, plus the named live
   acceptance requirements. A main `prod_issues: PASS` does not supersede this
   review: the vendor documents production-issue gating for attributed release
   scopes. Unattributed errors and absent release snapshots require investigation.

Readiness remains blocked while a relevant current defect, unresolved production
incident or required acceptance lacks evidence. This is the project's release
decision; do not present it as a newly computed native Tiden verdict. Classifying
future proposals alone does not repair runtime incidents or establish readiness.

For issue triage, read `issue fix-context`, every suspect source path and the
relevant historical revision. `verified` on a suspect spec is not proof that its
tests cover the incident. Report truncation. Preserve redaction and issue history;
resolve only after a matching regression is demonstrated and the repair verified.
Deployment acceptance remains separate, and old `lastSeen` alone proves nothing.

### Names and release identity

DeedKit is the platform; AnyMsg is the compatibility identity of its messaging
package. Current messaging telemetry constructs `anymsg@` plus
`messaging_service.__version__` (0.9.19 at the reviewed revision). It is a module
version, not a platform deployment SHA. The Tiden product's historical `ANYM`
prefix and existing component/test identities likewise survive the rename.

Keep historical release records and links. A future build-identity change must
coordinate the runtime release string, CI release record, environment, source SHA
and build URL. Do not relabel old events or manufacture a production release to
obtain a gate. Contributor headings and display text can use DeedKit immediately.

### Implementation sequence

1. Read this guide and `TIDEN-CI.md`. Fetch the relevant current Tiden main specs
   and inspect open intent branches/PRs before creating anything. A match in an
   unmerged branch is still an existing identity, not a reason to duplicate it.
2. For product changes, use `product-context`, read the code its results point
   to, and only then run `start-intent`. Promises are frozen when the session
   starts, so phrase them from what the code actually does. Keep promises about
   observable behavior; never invent a product requirement merely to track a
   documentation edit or bookkeeping session. Use an explicit review branch for
   a bulk spec/backlog maintenance import.
3. Implement in the appropriate Git branch. Update the existing requirement when
   its behavior changes; create a child only for an independent increment.
   Preserve preimages, historical source anchors and accepted test links.
4. Run the appropriate synthetic checks and publish authentic results through
   the existing reporter with exact checkout SHA and branch. Open the pull request
   with the `Tiden intent branch:` line the contract prints; CI routing uses it
   (TIDEN-CI.md). Never rename old
   tests or the seven `anymsg-ci::*` checks. Review assertions before proposing
   links: even an exact source-file match does not prove a whole specification.
5. Review the Tiden merge preview, conflicts and link proposals. Record a Git PR
   and Tiden branch together. A closed intent session or merged Git PR does not
   merge its Tiden branch. Merge reviewed spec changes under the merge delegation
   rule in `AGENTS.md`; never blanket-merge unrelated open branches.
6. Read back actual Tiden main IDs, sources, links, gate and GitHub drift after
   merging. A green branch projection is not a main result. `no_test`, `not_run`
   and failed executions remain visible. Do not create passing evidence or
   accept risk to compensate for a bookkeeping gap.
7. Close only the implemented increment. Keep rollout, capacity, actual host
   wake-up, provider completeness and empirical quality acceptance open until
   their own evidence exists. Update the Tiden item at the same time as the code
   review; do not create a replacement checklist in Markdown.

## Reconciliation and maintenance

Before a bulk change, export complete specs/sources and test identities/links
with `scripts/tiden_snapshot.py`; retain private snapshots outside Git. Its two
complete reads must agree. Network or pagination failure is an incomplete export,
not permission to claim preservation from equal counts.

Reconcile already merged code's spec branches before importing new work. Review
each proposed link and scope boundary. Leave experiments with open code PRs in
their own branches. An old recovery branch can contain duplicates of the present
baseline; preserve its history and record the disposition instead of merging
duplicates or deleting requirements to clean a dashboard.

After the update, compare exact object/link identities and historical anchors,
with a reviewed allowlist of intended additions and changes. Use
`scripts/verify_tiden_preservation.py` for complete snapshots. Report source
coverage, assertion review and live acceptance as distinct audit depths.

The observed CLI 0.33.0 source update/merge path regenerates source-record IDs
and does not round-trip nested `reverse_pr` metadata. Preserve each affected
original ID in `metadata.historical_source_id` and its complete original metadata
as serialized JSON in `metadata.historical_metadata_json`, alongside the original
source fields. Read these values back before a bulk update, and verify them after
merge. This is explicit provenance preservation, not permission to drop metadata
or to change specification, test or accepted-link identities. Do not overwrite
an existing provenance receipt during later updates.

Drift compares Git main with a reviewed spec-sync watermark. Advance it only
through a genuinely reviewed synchronization covering the named revision range;
do not use an empty sync/bootstrap to dismiss behavioral changes. A new commit
after the audited target is fresh drift, not a reason to claim the old review
covered it.

Useful read-only commands (pass the existing product/workspace explicitly when
the worktree is not bound):

```sh
tiden spec list --branch main --include-sources --format json
tiden branch list --format json
tiden branch merge-preview --name <branch> --format json
tiden coverage-gaps --product-id <product-id> --branch main --format json
tiden gate check --product-id <product-id> --format json
tiden drift status --product-id <product-id> --format json
```

Honor pagination and save complete JSON before summarizing it. A CLI failure is
unknown state. Do not infer completion from a truncated display or a past chat.

## Vendor references

- [Specs and structured provenance](https://docs.tiden.ai/docs/product/specs)
- [Recommended operating model](https://docs.tiden.ai/docs/product/overview)
- [Requirement import](https://docs.tiden.ai/docs/how-to/import-requirements)
- [Branch review and merge](https://docs.tiden.ai/docs/how-to/manage-branches)
- [Quality gates and traceability](https://docs.tiden.ai/docs/product/quality-gates)
- [Agent configurations and intent capture](https://docs.tiden.ai/docs/how-to/configure-agents)

The installed CLI help and shipped skills resolve version-specific commands;
the project rules above define how DeedKit uses them.
