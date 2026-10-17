# History depth and setup preview

Planned account setup offers `new_only`, calendar-month `window`, explicit
`since`, or `all_available`. Three months is a suggested default, not a maximum.
This is a **standalone preview**. Current collectors (0.9.19) do not execute these
waves or accept `history_policy`. Telegram retains `history_since`; WhatsApp
retains existing checkpoints. Do not put preview fields into live collector JSON.

From this service directory:

```sh
.venv/bin/python -m messaging_service.history_plan --months 12 \
  --anchor 2026-10-14T00:00:00Z
.venv/bin/python -m messaging_service.history_plan --new-only \
  --anchor 2026-10-14T00:00:00Z
.venv/bin/python -m messaging_service.history_plan --since 2025-01-01T00:00:00Z \
  --anchor 2026-10-14T00:00:00Z
.venv/bin/python -m messaging_service.history_plan --all-available \
  --anchor 2026-10-14T00:00:00Z
```

No credentials, session, database or provider calls are opened. Save the explicit
anchor at activation. Windows use UTC calendar months, clamping month ends.
Restoring the same anchor reproduces the plan; never advance it on a restart.
Timezone-aware ISO timestamps normalize to UTC.

For a year at this example anchor:

| Coverage goal | Historical fetch interval `[from,to)` |
| --- | --- |
| Week | 2026-10-07 → 2026-10-14 |
| Month | 2026-09-14 → 2026-10-07 |
| Quarter | 2026-07-14 → 2026-09-14 |
| Year | 2025-10-14 → 2026-07-14 |

The future runner gives every permitted chat a turn before deeper work. Later
bands avoid redownloading the recent part. Provider pagination still needs
stable ID cursors/overlap/commit fences. Preview does not create/claim jobs,
select the current wave or mark any chat traversed.

Pure contract: `compile_plan(policy, anchor=aware_datetime)`. Secret-free policy:
[`history-policy.example.json`](deploy/history-policy.example.json).
Month depth is integer 1–1200; custom earlier dates use `since`. Standard goals:
week, 1/3/12/24/60/120 months, then the chosen exact bound. `all_available` adds
an open final band without asserting all phone history is available. `new_only`
has no bands. Results say `preview_only=true`, wave `coverage=not_started`, and
`full_history_verified=false`.

Changing depth retains archived data. Durable setup/jobs still need policy
revisions, expansion, exclusions, fairness, provider cooldowns and gap recovery.
Message/original/processing coverage stay separate. Selected depth is a request,
not a guarantee of available data.

Durable wave execution is planned; only the policy compiler is implemented.
Provider recovery is bounded by the history available to each connector.
