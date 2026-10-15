# Calls source import

Imported the separately developed voice service from the owner's Personal OS source tree.
The source had no independent Git history or service license; the source file SHA-256
manifest records the exact 62-file snapshot. Private state, tokens, transcripts,
learning records, environments and built frontend artifacts are excluded.

On 2026-10-15 the owner explicitly approved MIT licensing for this module in the
migration conversation. `services/calls/LICENSE` records the same GoSync MIT terms
as the platform. Dependency licenses remain their own and are not relicensed.

The initial import retains flat imports, including the local `anymsg.py` adapter.
Run the service in its own environment and working directory. The public AnyMsg
SDK is not imported into this runtime. Import alone does not enable multi-agent
control: the platform adapter requires principal ownership enforcement first.
