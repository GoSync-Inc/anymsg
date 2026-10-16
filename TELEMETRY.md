# Optional Tiden runtime diagnostics

CI results and runtime error tracking are independent. Runtime events do not add
test coverage or prove that a feature works. Error tracking is off by default.

DeedKit is the platform name. Messaging events currently use `anymsg@0.9.19`,
constructed from the retained `anymsg` package version in
`messaging_service.__version__`. This is not an exact deployment identifier:
multiple commits can emit the same version. Preserve historical releases and
their issue links. Any future release-identity change must coordinate runtime,
CI, environment, source SHA and build URL. See the
[Tiden readiness workflow](docs/operations/tiden-product-workflow.md).

Install `anymsg[telemetry]` or build the intended Docker target with
`--build-arg ANYMSG_INSTALL_TELEMETRY=1`. This uses the official, hash-pinned
`tiden-telemetry` Python SDK. No browser JavaScript SDK is required for AnyMsg.

Store the product ingest DSN in an owner-protected file readable by the runtime
UID. Set `ANYMSG_TIDEN_ENABLED=1`, `ANYMSG_TIDEN_DSN_FILE` to that file and
`ANYMSG_TIDEN_ENVIRONMENT` to `production`, `staging` or `synthetic`. Do not use
an administrative API token as the ingest DSN or commit either credential.
The optional `deploy/compose.telemetry.yaml` configures the gateway. Keep all
existing overlays and rebuild/recreate only the intended service. Workers on an
internal-only network also need an explicitly configured outbound network.

Gateway/MCP unexpected errors, fatal Telegram/WhatsApp collector errors,
unexpected indexer/enricher errors and subscription matcher errors have capture
points. Expected request validation/authorization failures and ordinary provider
retries are not automatically reported. The AI inference child is not connected.
Node/Chromium crashes are reported through the existing Python collector owner;
there is no second WhatsApp client and no browser session instrumentation.

The payload contains the AnyMsg version, environment, fixed component/error code,
allowlisted exception type and relative public Python filenames/line numbers.
It excludes exception text, cause chains, locals, source lines, absolute paths,
HTTP bodies/headers/URLs, account/chat/person identifiers, search terms, messages,
files and model inputs/outputs. SDK request/user/breadcrumb extras are discarded.
Full source, logs and detailed data remain on the operator's host.

Delivery uses one background thread with a 32-event memory queue, at most 20
accepted events per minute per process and duplicate suppression for 60 seconds.
HTTP has a one-second timeout; the official SDK honors 429 backoff. Monitoring
does not wait in the request path, retry archive operations or write to the DB.
Events can be dropped during outages, overload or shutdown. Accepted locally is
not a delivery receipt: verify the event in Tiden Issues. Shutdown joins for at
most 1.5 seconds. If an ingest DSN is unreadable/invalid or the optional SDK is
missing, monitoring stays off and a content-free local warning is emitted.

Test with a synthetic exception under environment `synthetic`; confirm receipt
and resolve that diagnostic issue. Never provoke a real outage or use a personal
message/file as a test. Disable by removing `ANYMSG_TIDEN_ENABLED` and recreating
only the monitored service. Core archive functions require no Tiden access.
