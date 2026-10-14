# Optional Codex subscription connector

Implemented locally; not enabled by ordinary startup or deployed to production.
The connector is `provider: codex_cli` in the existing enrichment worker.
See [disabled example](enrichment.codex.example.json).

Codex runs on the worker host, but inference uses OpenAI over the network.
ChatGPT sign-in uses subscription access; an API key uses separately billed API
access. This connector requires ChatGPT sign-in and does not fall back to API
credentials. Subscription usage is shared with the owner's other Codex work,
not an unlimited batch-processing entitlement. The upstream docs describe this
as an advanced workflow for private trusted runners. Keep credentials and private
runner settings outside the public open-source repository.

## Results and supported inputs

- Images: PNG, JPEG, WebP. Short title/description/category and legible visible text.
- Documents: already extracted source text. Short title/summary/category and
  detailed **model analysis**, retained separately from the deterministic original
  text and its page/sheet/cell coordinates. No original PDF or office package is
  handed to the coding agent to execute or open with tools.
- Audio: continue using the selected ASR provider, e.g. local Whisper medium.
  This connector does not transcribe audio or replace existing ASR derivatives.
- Videos and files without usable extracted text are not automatically understood.
  They need a separate bounded extraction/frames/transcript stage.

Results remain model observations. `metadata.text_kind` is `model_analysis` for
documents and `visible_text` for images. A retained analysis is not a full exact
transcription. Long source text is bounded by `max_text_chars`; the worker keeps
its input truncation flag. The first implementation does not claim whole-document
analysis for files exceeding this limit. Future chunk/reduce jobs must cite actual
extraction coordinates and preserve partial coverage.

## Runtime boundary

Use a **dedicated private container/OS user**. The `isolated_runtime: true` field
is an operator assertion, not a mechanism that creates this boundary. The CLI is
a coding agent; a read-only sandbox alone is not a secret-read boundary.

Mount no messenger sessions, general Codex home, Docker socket, operator SSH keys,
API keys, or unrelated workspace. Give the queue worker only its dedicated DB
role, read-only originals, and the dedicated CLI authentication directory. The
native child receives no DB credentials or ambient API keys. For a stronger
boundary, stage one verified input into a separate model runner without DB/blob
mounts and ingest its JSON through the queue controller.

Install and pin a compatible official CLI in the image; never install at job
runtime. Create a dedicated `CODEX_HOME` directory with mode `0700` and an
`auth.json` with mode `0600`, owned by that runtime. Complete the official owner
login on that private runner. Do not copy the developer's general Codex home to
the server. Let the runner persist refreshes in its dedicated private directory;
never commit auth, paste codes/tokens into chat, or place it in a release artifact.
The adapter itself does not log in or transfer authentication.

Each invocation explicitly uses:

- `--ignore-user-config`, `--ignore-rules`, `--ephemeral`, a fresh private job cwd;
- `--sandbox read-only`, approval policy `never`, forced ChatGPT auth;
- disabled shell/unified exec, apps, plugins, hooks, agents and browser;
- no MCP servers or hosted web search, no project instructions;
- source text on stdin or one controlled image; no shell interpolation;
- structured final JSON with four bounded fields; no arbitrary result keys;
- rejection of incomplete/failed runs and any observed tool-use item.

The containing enrichment worker already limits native child CPU/address space,
output file size and wall time, kills its process group on expiry and fences writes
by lease, source digest, revision, active attachment and message deletion. Do not
invoke this low-level connector on an untrusted corpus without that controller
and the separate OS/container boundary. The CLI's own model requests require
egress; its command network is disabled. Restrict runner egress separately.

`max_output_tokens` is a common API connector field, **not a verified hard token
cap for Codex CLI**. Codex outputs here are bounded by a JSON schema and response
byte/character checks, plus runtime and attempted-job limits. Do not present those
as a monetary or subscription-usage guarantee.

## Before enabling beside local ASR

The current enrichment budget table counts attempts globally. A second worker
with a small Codex limit would see ASR attempts in that counter. Separate budgets
by profile/provider before enabling both concurrently. Also add a persistent
profile-wide pause for authentication failure, usage exhaustion and required CLI
upgrades; per-file retries must not spin over the entire corpus. The adapter
returns safe `model_rate_limited`, `codex_chatgpt_login_required`,
`codex_upgrade_required` and other code-only failures.

Keep the example disabled until these controls, isolated deployment and owner
login are complete. Start with a small representative set and measure quality,
attempts, latency and shared usage before enabling all accounts/files. Nothing
automatically changes the live audio profile.

## Validation

Unit tests cover opt-in, auth permissions/symlinks, API fallback rejection,
ambient credential removal, controlled invocation, supported image types,
structured result validation, timeout redaction, incomplete/tool-use rejection
and safe error classification. A real synthetic-text smoke must also pass with
the pinned CLI and chosen model before deployment. A text smoke is not a vision
quality benchmark or proof of production container isolation.

## Official sources

- [Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Authentication](https://learn.chatgpt.com/docs/auth)
- [Shared subscription usage](https://learn.chatgpt.com/docs/pricing)
- [CLI installation](https://learn.chatgpt.com/docs/cli)
