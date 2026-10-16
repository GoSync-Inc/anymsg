# Parallel development and releases

This document covers how several agent sessions work at once and how their work
reaches production. Tiden usage is defined in
[tiden-product-workflow.md](tiden-product-workflow.md), promotion in
[production.md](production.md), and review levels, reviews and merge authority
under Review levels in [AGENTS.md](../../AGENTS.md). Do not restate those rules here.

## Roles

- **Owner** sets priorities, decides owner-level pull requests, authorizes
  production apply and every live provider action. Nothing below delegates that
  authority implicitly.
- **Orchestrator** (one at a time) selects ready stories, checks lanes, dispatches
  worker sessions and checks their pull requests before they merge. It writes no
  product code.
- **Worker session** (Codex or Claude Code) implements exactly one story in its own
  worktree, branch, pull request and Tiden intent session or recorded skip.
- **Release owner** (one at a time) prepares and applies promotions of `main`.

## Unit of work

A story is **ready** when its Tiden record has the fields the Tiden workflow
requires (acceptance criteria, dependencies, priority, evidence to close) plus its
**lane** and expected paths. Stories that are not ready go back to the owner. A
change that takes `tiden intent skip` has no story; its brief names the goal and
paths instead.

One story maps to one Git branch, one pull request and one intent session. Split
larger work into stories that merge independently. A pull request should merge
within about a day; the older a branch, the more it conflicts.

**Claim.** A worker opens a draft pull request with its first push. Its description
names the lane and review level (`Lane: <lane>`, `Review level: <level> — <reason>`)
and carries the `Tiden intent branch:` line or `Tiden: skip — <reason>`. The draft
pull request is the claim. Before dispatching, the orchestrator lists open pull
requests and open intent branches; an item with a claim is taken. A claim without
activity for two days is released by the orchestrator after asking the owner.

## Lanes

Two concurrent sessions must not share a lane. The lane is recorded in the story
and in the pull request description. The orchestrator compares the planned paths
with the changed paths of open pull requests (`gh pr diff <n> --name-only`) before
dispatch.

| Lane | Paths | Concurrent PRs |
| --- | --- | --- |
| `schema` | `services/messaging/messaging_service/migrations/`, any `schema.sql` or `*_role.sql` | 1 |
| `messaging-mcp` | `services/messaging/messaging_service/app.py` | 1 |
| `messaging-store` | `services/messaging/messaging_service/store.py` | 1 |
| `telegram-ops` | `services/messaging/messaging_service/telegram_operations.py`, `telegram_collector.py` | 1 |
| `whatsapp-native` | `whatsapp/whatsmeow/` | 1 |
| `release-infra` | `deploy/`, `scripts/production.py`, `scripts/seamless_release.py`, `services/release-router/`, `.github/workflows/`, Dockerfiles, lock files | 1 |
| `process-docs` | `AGENTS.md`, `CLAUDE.md`, `.claude/`, `TIDEN-CI.md`, `BACKLOG.md`, `docs/operations/` | 2, disjoint files |
| module lanes | `services/calendar/`, `services/calls/`, `services/gateway/`, `packages/`, `whatsapp/` browser collector | 2 each, disjoint files |

The first five lanes exist because single large functions or numbered files make
parallel edits collide: `create_app` holds every messaging MCP tool, provider
operations live in one dispatcher per provider, and migration numbers are
sequential with immutable checksums. Splitting those functions is itself a
`messaging-mcp` / provider-lane story and would raise the limits afterwards.

A story that needs two lanes holds both, and several lanes make its review level
at least important (`AGENTS.md`). Prefer splitting it.

## Merging

- **Merge queue: not applied yet.** CI already runs the required checks on the
  `merge_group` event, and a queue run publishes nothing to Tiden
  ([TIDEN-CI.md](../../TIDEN-CI.md)). As of 2026-10-16 the repository has no
  ruleset: `main` keeps the classic protection in `deploy/main-protection.json`,
  with strict up-to-date branches. Moving `main` to a ruleset with the merge queue
  changes branch protection, so it is an owner-level change in the `release-infra`
  lane, together with `deploy/main-protection.json`. Update this section when it
  lands.
- Until then a pull request must be up to date with `main` to merge. Update a
  branch right before merging, to resolve a real conflict or when a review asks
  for it, never only to catch up. With the queue, each pull request is tested
  against the latest `main` plus the pull requests ahead of it, and a branch is
  updated only for a conflict or a review. After merging `main` into a branch,
  close the intent session on its own commit range
  ([Claude Code guide](claude-code-tiden.md)).
- Merge method: **squash**. One story becomes one commit on `main`.
- Who merges follows the review level in `AGENTS.md`; a worker also waits for the
  orchestrator's "ok" (Worker protocol). Enqueueing a pull request is merging it.
  Never bypass required checks or use administrator merges.
- Merging the Git pull request never merges its Tiden branch. That branch merges
  separately, under the same review level and conditions.

## Schema compatibility

- **Expand, then contract.** A migration must work with both the currently deployed
  code and the new code. Dropping, renaming or narrowing happens in a later release,
  after no deployed code uses the old shape. Blue-green handoff and image rollback
  depend on this.
- Never edit a merged migration. Reserve the next migration number in the pull
  request title while the `schema` lane is held.
- A schema migration is owner-level (`AGENTS.md`). Its pull request states its
  backup prerequisite, and its release waits for the backup gate in
  [production.md](production.md).

## Releases

- Merging and deploying are independent. A merge never implies a deploy, and a
  pending deploy never blocks merges.
- The release owner promotes an exact `main` SHA by [production.md](production.md)
  once the readiness decision in the Tiden workflow is recorded for that SHA, with
  live-session authority where the changed service requires it.
- One release is in flight at a time; the host deployment lock enforces it.
- A new `main` push run cancels the one still running (`ci.yml` concurrency), so a
  SHA merged shortly before another has no successful run and cannot be promoted.
  Promote the latest SHA whose run succeeded.
- Release at least weekly while changes are merged, so production does not drift
  far from `main`. Each release keeps production.md's receipts and the readiness
  decision for its SHA.

## Orchestrator loop

1. Read Tiden main and open proposal branches; list ready stories by priority.
2. List open pull requests and intent branches; compute busy lanes.
3. Dispatch to free lanes, up to the WIP limit. Start with three worker sessions
   plus the `release-infra` and `process-docs` owners; raise the limit only after
   the queue and lanes have worked for a week.
4. Each brief names: story link or goal, lane, expected paths, review level,
   acceptance criteria, checks to run, the evidence to return and the final result
   its level requires.
5. Answer workers' questions and check each pull request before its "ok" (Worker
   protocol).
6. After merge: read back Tiden main and drift, update the story, free the lane.
7. Escalate to the owner: priority conflicts, owner-level pull requests, every
   escalation condition under Review levels in `AGENTS.md`, any story that is not
   ready.

## Worker protocol

The orchestrator starts each worker in its own Superset workspace, with its own
worktree and branch, and keeps a table: task, dependencies, workspace, branch,
status, result. It picks the worker's model and effort by the kind of work:

| Work | Model | Effort |
| --- | --- | --- |
| Code, CI, security | Opus | xhigh |
| Tiden specs and backlog | Opus | high |
| Documents and settings | Sonnet | high |
| Orchestrator | Opus | high |

Reviewers follow Review levels in `AGENTS.md` (Astra and Fable) whatever the
worker's model.

The worker writes and waits for the answer:

- before `tiden intent start`: its objective and promises, which cannot be edited
  after the start;
- before going out of scope: another task's file, an owner-level action, or a fork
  without an obvious answer;
- before merging: "ready to merge" with the pull request, CI, review results and
  the Tiden verdict. The orchestrator checks CI, the diff and the Tiden merge
  preview itself and only then answers "ok"; it hands an owner-level pull request
  to the owner.

The worker writes without waiting: at the start, when it opens the pull request,
and its final result. The result ends with a `SUPERSET_WORKER_DONE` or
`SUPERSET_WORKER_BLOCKED` block. Both list `task`, `summary`, `files` and `checks`;
DONE adds `handoff`, BLOCKED adds `reason` and `needs`.

The final result depends on the level:

- micro: merged after green CI;
- normal and important: the pull request and its Tiden branch merged and checked
  after the merge, the intent session closed with PASS or a recorded skip;
- owner: the pull request ready (not draft, green CI, reviews with no open
  findings, Tiden closed or skipped) and a summary for the owner in its
  description. After the owner merges it, the worker finishes the Tiden steps.

After its final result the worker stays open until its pull request is merged and
checked after the merge. The orchestrator then closes its terminal and archives
the workspace without asking again, after checking that the worktree has no
uncommitted changes: archiving deletes the folder, the branch stays.

Claude Code workers and the orchestrator talk through messages between sessions.
A Codex worker asks in its terminal, which the orchestrator reads. The
orchestrator checks a silent worker when it gets the idle notice; there are no
timed reports.

## Sessions

- Every session uses its own worktree and branch. Never share a worktree and never
  use bare `git stash`; the stash stack is shared across worktrees.
- Codex and Claude Code follow the same rules. Tool-specific setup lives in
  `CLAUDE.md` and the [Claude Code Tiden guide](claude-code-tiden.md).
