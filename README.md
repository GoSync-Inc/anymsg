# AnyMsg

**Self-hosted Telegram and WhatsApp archive for AI agents. A GoSync open-source project.**

Collect messages and attachments once, then let agents search and read them through
MCP or HTTP with separate credentials and account/chat permissions. Original files
use SHA-256 deduplication; references to every source message stay separate.

Current version: **0.9.8 — experimental**, PostgreSQL schema 21.

## What works

- Multiple Telegram and WhatsApp accounts, each with its own session and collection state.
- PostgreSQL full-text search, including Russian word forms, people/usernames,
  chat/date/sender filters, nearby context and direct provider references.
- Searchable file text, PDF/Office/table extraction, bounded OCR and optional
  audio transcription or image/document enrichment through local or API profiles.
- Per-chat history coverage, collector errors and separate download/extraction/analysis states.
- Scoped agent keys, independent acknowledgements, immutable review records and revision evidence.
- Durable collection queues, storage admission, processing budgets and a shared heavy-job lock.
- Encrypted streaming backup with integrity checks and quarantine restore tools.

Message sending is **not implemented** in this release. Reads do not mark messages
as read in Telegram or WhatsApp. Empty search results do not prove complete history.
Durable history-wave execution, cold-file eviction/rehydration and independent
AI artifact reuse are planned. Optional cloud processing is off until explicitly configured.

## Distribution

- **Python `anymsg`**: server, operator CLI and HTTP client. Internal `messaging_service`
  imports, database names and migration checksums remain compatible.
- **npm `anymsg`**: dependency-free JavaScript HTTP client and CLI for an existing server.
  It does not install PostgreSQL, Chromium or a second messenger session.
- **Source / Docker**: [server setup](SETUP.md) and explicit collector overlays in `deploy/`.

For a source installation with Python 3.13:

```sh
git clone https://github.com/GoSync-Inc/anymsg.git
cd anymsg
uv sync --group dev
uv run anymsg --help
```

The built wheel also installs outside the checkout. Registry publishing requires
maintainer account setup; see [RELEASING.md](RELEASING.md).

JavaScript client:

```js
import { AnyMsgClient } from 'anymsg';
const client = new AnyMsgClient({
  baseUrl: 'https://messages.example.com',
  token: process.env.ANYMSG_TOKEN,
});
const result = await client.search({ query: 'contract', limit: 10 });
```

Python client:

```python
import os
from anymsg import Client
with Client('https://messages.example.com', os.environ['ANYMSG_TOKEN']) as client:
    result = client.search(query='contract', limit=10)
```

Point a Streamable HTTP MCP client at `https://messages.example.com/mcp` and set
`Authorization: Bearer <agent key>`. Use each agent's own scoped key. The server
uses JSON POSTs; it does not provide a GET notification stream.

## Operations and limits

The tested server stack is Linux amd64, Python 3.13, Node 24 for WhatsApp and
PostgreSQL 18. Windows server execution is unsupported. Native host prerequisites
include PostgreSQL tools for backups and Node/Chromium for WhatsApp; a Python wheel
alone cannot provide those system services. Docker ARM support is not claimed.

Use separate deployments/databases for different owners. Multiple accounts in one
installation do not create a tenant security boundary. Never run two clients against
one provider session. History available to a linked WhatsApp device can be partial.
The WhatsApp adapter uses unofficial `whatsapp-web.js`; it is not the WhatsApp
Business API and can break or lead to account restrictions. Follow provider terms
and use appropriately authorized account/chat scopes.

See [ACCESS.md](ACCESS.md), [SEARCH.md](SEARCH.md), [FILE_INDEX.md](FILE_INDEX.md),
[COLLECTORS.md](COLLECTORS.md), [MEDIA_STORAGE.md](MEDIA_STORAGE.md),
[ENRICHMENT.md](ENRICHMENT.md) and [BACKUP_STREAMING.md](BACKUP_STREAMING.md).
Run synthetic tests only: [CONTRIBUTING.md](CONTRIBUTING.md).

## License and security

MIT for original GoSync code. Dependencies retain their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and `deploy/MOBY-LICENSE`.
Report vulnerabilities privately through [GitHub security advisories](https://github.com/GoSync-Inc/anymsg/security/advisories/new),
not by posting session data or tokens in an issue. See [SECURITY.md](SECURITY.md).
