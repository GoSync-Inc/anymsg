# iCloud connection and pilot

The pilot creates and changes only fictional events in a chosen test calendar. It does not invite participants, does not change sharing and does not move real family meetings.

## 1. Prepare the Apple Account

1. Turn on two-factor protection for the Apple Account.
2. Open [account.apple.com](https://account.apple.com), the **Sign-In and Security → App-Specific Passwords** section, and create a password named `Family Calendar Pilot`.
3. Use this password for the local connection. The service does not need the main Apple Account password. The procedure and the mandatory 2FA are described by [Apple](https://support.apple.com/en-us/102654).
4. Create a **new empty calendar Family Calendar Pilot** in iCloud specifically. It is convenient through [icloud.com/calendar](https://www.icloud.com/calendar); [Apple's instructions](https://support.apple.com/en-ie/guide/icloud/mmfbbb2cf9/icloud). In the Apple "Calendar" app choose the iCloud section: a calendar "On My Mac" is not available to the CalDAV server.

A Google calendar shown by the Apple app is connected later through the Google API. This pilot needs an iCloud source.

## 2. Enter credentials locally

From the repository root:

```sh
uv sync --locked
uv run family-calendar connection add-icloud --id pilot-a
uv run family-calendar connection list
uv run family-calendar pilot icloud discover --connection pilot-a
```

Enter the Apple Account email, then the app password at the hidden terminal prompt. Do not send the password in chat, add it to command arguments or to Git files. Without an interactive terminal, input is disabled.

Discovery returns `read_verified: true` and a `calendars` array with `ref`, title and `privilege`. This confirms that discovery can read, not that all events can be read or that writing is allowed. Find **Family Calendar Pilot** and copy its `ref` of the form `cal-…`. If the calendar is not found, check the iCloud source and repeat discovery; do not pick a personal calendar instead.

One account does not prove access to another. The second is connected with a separate ID and its own password:

```sh
uv run family-calendar connection add-icloud --id pilot-b
uv run family-calendar pilot icloud discover --connection pilot-b
```

A repeated `add-icloud` with an existing ID does not overwrite credentials. If the password has been revoked, connect a new separate ID and run discovery/allowlist again. Keep old reports; moving an existing journal to another ID is forbidden.

## 3. Allow writing to the test calendar

In all commands below replace `CALENDAR_REF` with the ref found. The example path `outputs/icloud-pilot-01` is a new private directory for one run.

```sh
uv run family-calendar pilot icloud allow-write --connection pilot-a --calendar-ref CALENDAR_REF --test-calendar
uv run family-calendar pilot icloud run --connection pilot-a --calendar-ref CALENDAR_REF --allow-write --run-dir outputs/icloud-pilot-01
```

`--test-calendar` records the operator's choice of a separate test calendar. The allowlist applies to the exact connection/calendar pair; server-side `read_only` forbids writing. With `unknown` privileges, the right stays unverified until a real write. The `--allow-write` flag does not create an allowlist by itself.

The matrix is limited to 16 created objects (usually 13), with separate UID/href. Most events start on the next Monday at 16:00 Asia/Makassar. The Europe/Berlin timezone fixtures start on 19 October 2026 and cross a DST change; this is an intentional fixed check date. An event name starts with `[Family Pilot]`. The single CRUD event is deleted during the test; the others remain until checking and cleanup.

## 4. Check the report and the Apple client

```sh
uv run family-calendar pilot icloud status --run-dir outputs/icloud-pilot-01
uv run python scripts/check_pilot_evidence.py --run-dir outputs/icloud-pilot-01 --allow-unaccepted
```

The first command sends nothing to the network. The checker script verifies scope, intent hashes, the actual read-back ICS and the state of the steps. `--allow-unaccepted` checks integrity before visual acceptance. This is a local check of the evidence, not a signature or independent confirmation by the provider.

Wait for the Apple "Calendar" app or the iCloud UI to sync. Find the starting Monday from `manifest.json`. Check:

| Scenario | What to see |
|---|---|
| Single CRUD | After create/update/delete the event is absent; the steps are confirmed by the journal |
| Single/cancel/series (`exceptions`) | The second meeting is individually moved to 18:00, the third is cancelled; after the series shift the rest are at 17:00, the individual time is kept |
| Future COUNT/UNTIL | Past meetings at 16:00, future ones at 17:00; the moved fifth at 18:00, the third cancelled; five meetings in total |
| Future infinite | The same behavior at the boundary; the future stays an unbounded series |
| Native RANGE (`native-range`) | Starting from the second meeting the new time is 17:00; also check later weeks |
| All-day | The event is moved to the next day; duration one day |
| Floating | Local time is displayed consistently; interpretation for slot search is still disabled |
| TZ/DST/properties | Europe/Berlin keeps 17:00 on both sides of DST; a 15-minute alarm; EXDATE/RDATE are not lost |

X-properties are checked by the server read-back, since the Apple UI may not show them. A server echo of `RANGE` without correct client behavior does not prove native future support. If the native probe does not work, note that in the result; the split future is checked independently.

The `VTIMEZONE` comparison for `Asia/Makassar` and `Europe/Berlin` uses the exact sequence of UTC transitions in the interval 2020–2035. This lets iCloud replace the zone's history with its own equivalent description; changes of time, rules, alarms and event properties are still distinguished. Other zones, additional zone properties and complex definitions are compared in full. Behavior outside this interval, including the far future of unbounded series, is not confirmed by the pilot. Generating the definition uses dateutil for the correct moment of the spring DST transition.

Save an honest core result and separate future/native results, as well as the client name/version. Example for three successful checks:

```sh
uv run family-calendar pilot icloud record-visual-check --run-dir outputs/icloud-pilot-01 --result pass --future-result pass --native-range-result pass --checked-all-cases --client 'Apple Calendar, version from About'
uv run family-calendar pilot icloud reconcile --run-dir outputs/icloud-pilot-01
uv run python scripts/check_pilot_evidence.py --run-dir outputs/icloud-pilot-01
```

`--result` applies to core (single, conditional, single/cancel/series, all-day, properties/TZ); future and native have separate flags. Use `fail` instead of `pass` if an error is found, or `not-checked` for a future/native not yet checked. `--checked-all-cases` confirms that all applicable core cases were viewed. By default the mark is stored as `owner_reported`.

If the agent checked the client through Computer Use, use `--source agent_observed --evidence outputs/icloud-pilot-01/observations.json`. The manifest contains `checks`: for each scenario viewed — `result` (`pass`/`fail`) and `artifacts` (relative paths to PNG/JPEG and TXT inside this run-dir). For example:

```json
{"checks":{"all_day":{"result":"pass","artifacts":["client-observed/all-day.png","client-observed/all-day.txt"]}}}
```

Full acceptance needs all five core scenarios, for future — additionally three future scenarios; for a verified native — `native_range_probe`. The example above shows only the format of one scenario. Each scenario requires a screenshot and the saved UI text; the files must be owner-only. On recording, the SHA-256 of the artifacts is saved. The report and evidence checker reject missing, changed or out-of-run-dir artifacts. This is a local integrity check of the agent's observations, not an independent Apple signature or automatic image recognition. The `agent_observed` source is not passed off as owner confirmation.

`reconcile` may continue matrix steps that have not yet been executed in the previously allowed calendar. Do not treat it as a read-only command.

`gate_a=accepted_core` allows further core development; `accepted_core_and_future` additionally confirms the simple weekly split. `not_accepted` requires a fix/recheck. The mock environment is always `not_accepted`. The second account and a shared calendar stay `unverified` until a separate live test. A failed native probe does not disable the confirmed split; native stays unverified and is not used for ordinary moves.

## 5. Failure and command retry

- Keep the **same** `--run-dir`: UID/href and intent are pinned. A new directory means a new set of events.
- On a timeout, run `status` first, then `reconcile`. An unknown mutation is not repeated automatically; the read-back either confirms the target or leaves `needs_attention`.
- The absence of an object right after a timeout does not allow repeating the write. Repeat reconciliation later; if uncertainty continues, keep the object/report for analysis.
- On `version_conflict` someone else's edit is not overwritten. Such a step is not reset to `prepared` automatically.
- `conditional:stale-version` with 412 is an intentional negative test; other conflicts need analysis.
- After 401/403 or revocation of the local write permission, further new mutations in this matrix are blocked.
- Do not run two live processes for one run-dir at the same time. The SQLite atomic claim protects a single step, but full worker leases and cross-account locks belong to the next stages.

The report uses `evidence_as_of`: repeating the command does not turn a historical read-back into a fresh check of the external state. The `generated_at` time denotes only when the report was built.

## 6. Clean up and disable writing

After the evidence is saved and the visual check is done:

```sh
uv run family-calendar pilot icloud cleanup --run-dir outputs/icloud-pilot-01
uv run family-calendar pilot icloud disable-write --connection pilot-a --calendar-ref CALENDAR_REF
```

Cleanup deletes only verified-owned refs with matching last ETag and semantic hash. Manual changes, unknown results and objects that are missing without a deletion intent are not deleted automatically. Check `remaining` and `conflicts`; if non-zero, keep the journal for analysis. For a repeated cleanup, writing must still be allowed. Do not delete the whole calendar instead of checking the remaining objects.

After cleanup has started, the matrix cannot be run in the same directory (`run_already_cleaned`). For a new test — a new run-dir. Journals/reports can be kept as private historical evidence.

Disabling the allowlist does not revoke the Apple password itself. It can be revoked separately at account.apple.com. Apple also revokes app passwords automatically when the main password is changed; [source](https://support.apple.com/en-us/102654).

## Storage

By default `.local/` is created in the current working directory: credentials are encrypted with Fernet, the local key is stored separately in `.local/master.key`; files 0600, folder 0700. `outputs/` holds the private before/after ICS, the actual responses, hrefs, mappings and the SQLite journal. Both directories are excluded from Git and packages.

A local key next to the configuration protects against an accidental leak of a single file; a user with access to both can decrypt the credentials. This is a local owner-only pilot store, not a system Keychain or a production vault. Do not lose the key: the service does not generate a new one over existing credentials. For a run from another directory always pass `--home /absolute/private/registry` explicitly **before** `connection` / `pilot`; an existing directory needs restricted permissions.
