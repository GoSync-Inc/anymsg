# Family Calendar Service

Read README.md and docs/ICLOUD_PILOT.md before changing the pilot. The repository is separate from AnyMsg; do not change other services or credentials.

Use synthetic fixtures and mock CalDAV endpoints in normal tests. Real accounts require owner-run connection setup and an explicitly allowlisted test calendar. Never infer permission to modify other calendars. Never put passwords, emails, raw live ICS, hrefs or reports in Git. Private data belongs in ignored .local/ and outputs/.

Every network mutation has a durable intent and provider-supported preconditions: CalDAV create uses If-None-Match; updates/deletes use If-Match. Google insert does not support conditional headers: use a persisted client-generated unique ID, verify by that ID, and never resend an unknown insert. Unknown results must be reconciled before retry. Preserve ICS properties and exception identity; unsupported transformations fail explicitly. Live evidence is not manufactured by mock tests. No new agents or external messages without user authorization.

Stop owned temporary processes when no longer needed. Preserve other tasks' changes. Use test-first implementation and run relevant checks before committing.
