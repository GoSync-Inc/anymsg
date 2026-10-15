# Calendar history import

Imported the Family Calendar Service history at source commit
`3f06bc60531936cd8631f0dec7043b143d2cdcac` under `services/calendar` with `git subtree`.
The source repository was left unchanged. Its MIT license and attribution remain
in that directory. All 106 historical blobs were inspected for private runtime
paths and credential patterns before import; no flagged content was present.

Only tracked source history is included. Accounts, provider credentials, database
records, encrypted vaults, reports and local worker state are not in Git. The
`family_calendar` imports and CLI entrypoints are retained. Dependency upgrades
are separate from the import commit. Runtime data moves only after restore checks.
