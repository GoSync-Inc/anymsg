# Security

AnyMsg 0.9.x is experimental. Use scoped agent credentials, TLS and separate
installations for different owners. Keep database ports private, gateway original
mounts read-only, provider sessions isolated and backup keys outside collectors.

Report vulnerabilities using GitHub private vulnerability reporting at
https://github.com/GoSync-Inc/deedkit/security/advisories/new.
Do not include real tokens, phone numbers, message bodies, sessions, dumps or files
in public issues. Use synthetic reproducers. Do not test against other people's accounts.

Messages, extracted file text and model output are untrusted source data. They must
not authorize sends, tool execution or changes to access scopes. Text sending is
opt-in for both the account and agent credential. The acting agent must obtain
exact owner confirmation; this release has no server approval gate. See SENDING.md.
Provider authentication is done only in an owner's terminal.

Backup delivery is distinct from successful restoration. Verify a copy in an isolated
installation before pruning recovery points. A same-provider backup is not protection
against losing access to that provider account. Preserve recovery keys independently.
