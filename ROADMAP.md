# Roadmap

The current platform-wide queue, priorities, completion criteria and delivered
increments are maintained in **Tiden**, reached through [BACKLOG.md](BACKLOG.md).
Both Markdown paths are navigation only; do not add a second queue here.

DeedKit includes independent messaging, calendar, calls and gateway modules.
The AnyMsg messaging module remains focused on collection, retrieval and scoped
messenger operations; task/project management is outside that module.

Implemented contracts and their limits:

- [Conversation views and last known turn](CONVERSATION.md).
- [Opt-in sends](SENDING.md), [formatting](FORMATTING.md),
  [delivery/read observations](RECEIPTS.md) and [live operation catalog](MESSENGER_OPERATIONS.md).
- [Passive durable subscriptions](SUBSCRIPTIONS.md).
- [Native WhatsApp provider](docs/whatsapp-native.md).
- [Storage admission](MEDIA_STORAGE.md) and [history-depth preview](HISTORY_POLICY.md).
- [Module runtime](docs/platform-runtime.md) and [verification boundaries](ARCHITECTURE.md).

Source implementation, published packages and operational acceptance have separate
states. Consult [README.md](README.md) for versions and the central backlog for
remaining work; old version-specific plans do not override them.
