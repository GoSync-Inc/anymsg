# Third-party notices

AnyMsg source is MIT licensed by GoSync. Dependency licenses remain their own;
installation from npm/PyPI does not relicense those dependencies. The pinned
lock files are the dependency inventory. Preserve dependency notices when
redistributing installed environments or container images.

Message formatting uses [markdown-it-py](https://github.com/executablebooks/markdown-it-py)
and [mdurl](https://github.com/executablebooks/mdurl) (MIT), pinned in the Python
lock files. Raw HTML parsing is disabled in Markdown.

Direct components include [Telethon](https://github.com/LonamiWebs/Telethon)
(MIT), [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
(MIT), [Psycopg](https://github.com/psycopg/psycopg) (LGPL-3.0),
[Starlette](https://github.com/Kludex/starlette), [Uvicorn](https://github.com/Kludex/uvicorn)
and [HTTPX](https://github.com/encode/httpx) (BSD-3-Clause),
[whatsapp-web.js](https://github.com/pedroslopez/whatsapp-web.js) and
[Puppeteer](https://github.com/puppeteer/puppeteer) (Apache-2.0).
Optional audio processing uses faster-whisper/CTranslate2; model weights have
separate terms and are not bundled. The npm HTTP SDK has no runtime dependencies.
Optional runtime diagnostics use [tiden-telemetry](https://github.com/qase-tms/tiden-telemetry-python)
(MIT), pinned in `requirements-telemetry.lock`. Sending is disabled by default.

The Chromium seccomp policy derives from Moby. Its provenance and Apache-2.0
license are retained in `deploy/chromium-seccomp-SOURCE.md` and
`deploy/MOBY-LICENSE`. The backup Docker target downloads the pinned age binary;
its BSD-3-Clause license is retained in `licenses/age-LICENSE` and that image.
Docker bases, PostgreSQL, Node, Chromium, OCR and audio system packages retain
upstream notices; Dockerfiles record installed package inventories. AnyMsg does
not ship those binaries in the wheel/npm tarball and does not publish container
images in this release. This is an inventory of direct components, not a claim
that every transitive dependency or model has completed a license audit.
