# Claude Code with Tiden

This is the Claude Code entry point for DeedKit's Tiden workflow. It adds only what
is specific to Claude Code. The rules live in
[tiden-product-workflow.md](tiden-product-workflow.md) (product work, readiness and
naming) and [TIDEN-CI.md](../../TIDEN-CI.md) (CI evidence). Requirements, priorities
and evidence live in the
[DeedKit Tiden product](https://app.tiden.ai/1dc18e09-4a09-4d74-8300-cd4302622357/products/75bd0580-2af4-4d5a-b236-c5a9d7ff7ce1);
this file is not a backlog.

## Setup check

Run `tiden doctor`. For Claude Code it must report the six skills, the Tiden allow
rules and the `claude-code` intent hooks. Skills are not in Git: check
`tiden skill list --agent claude-code --global` and `--local`, and install missing
ones with `tiden skill install --agent claude-code --global`, because a new worktree
has no local copy. Do not copy or edit skills by hand, and do not change other
agents' hooks, hook trust or credentials. When a worktree is not bound, pass
workspace `1dc18e09-4a09-4d74-8300-cd4302622357` and product
`75bd0580-2af4-4d5a-b236-c5a9d7ff7ce1` explicitly.

## Session sequence

1. Answer the per-prompt start directive yourself: continue a listed candidate,
   start a new intent, look up, or skip. Documentation, process and read-only work
   use skip.
2. Before `tiden intent start`, run `product-context` for each independent part and
   read the code its results point to. Promises are frozen at start and cannot be
   edited; a promise written before reading the code may later need a waiver.
3. After the first edit, run `tiden intent refine` with the session objective. Drop
   retrieval hits unrelated to the diff with
   `tiden intent radius drop <seq> --reason "<one line>"`.
4. Open the pull request with the exact `Tiden intent branch:` line from the
   contract in its description, then run `tiden intent link-pr`. CI publishes the
   pull request's evidence to that intent branch; without the line it lands on a
   branch named after the Git branch (TIDEN-CI.md).
5. Evidence comes from CI. While a run is in progress, answer the stop hook with the
   current `tiden intent progress` status and the run you are waiting for. A local
   run that bypasses the reporter does not change the gate.
6. After CI, run `tiden intent progress`, link reviewed tests, then use
   `capture-intent` (candidates, settle, waive) and `tiden intent close`. Defer a
   connected item that has no test yet with `--propose-tests`; never accept risk to
   clear an unrelated Backlog card.

## Where work goes

| Content | Place |
| --- | --- |
| Maintained behavior | Its existing spec on Tiden main; keep IDs, sources and test links. |
| Defect in maintained behavior | The current contract plus an issue or repair branch; it blocks readiness until a fix is proven. |
| New future capability | A separate proposal or intent branch per independently deliverable change. |
| Acceptance on a real installation | Separate from synthetic tests: version, environment, authority and checkable evidence. Unknown until then. |
| Navigation and legacy DK IDs | `BACKLOG.md`, without editable statuses or priorities. |

Split a mixed card first: what is promised and broken now, what needs an
installation check, and what is a new proposal. Keep its DK ID and source links.

## Production errors

Use `triage-issues` and the readiness rules in the workflow guide. Do not resolve an
issue on age, path proximity or a nearby green test. Never start a provider session,
send a message or weaken telemetry redaction to diagnose one.

## Final report

State separately:

- The native gate verdict, its scope and when it was computed.
- The exact SHA and environment, and whether a release gate exists.
- Remaining current defects, production issues and unverified acceptance conditions.
- Future proposals and the unfinished relocation of the old backlog.
