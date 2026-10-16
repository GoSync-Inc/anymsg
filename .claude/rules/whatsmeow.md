---
paths:
  - "whatsapp/whatsmeow/**"
---
<!-- Sources: docs/whatsapp-native.md, docs/quality/whatsmeow-parity.md, services/messaging/hatch_build.py, .github/workflows/whatsmeow.yml -->
# Native WhatsApp adapter (Go)
- Verify with `go vet ./... && go test -race -count=1 ./...` (Go 1.26.9, as pinned in go.mod and CI). After changing capture/envelope/IPC, also run root `tests/test_whatsmeow_contract.py` (set `ANYMSG_WHATSMEOW_TEST_BINARY` or have Go installed).
- The upstream pin `go.mau.fi/whatsmeow` is MPL-2.0. Do not edit upstream files. A bump also updates the pinned revision in docs/whatsapp-native.md and docs/quality/whatsmeow-parity.md.
- New non-test `.go` files must be added to `NATIVE_SOURCES` in `services/messaging/hatch_build.py`, or the sdist ships without them.
- Captures are journaled before ACK. Keep the fenced session owner lock (`native-owner.lock`), private dirs and separate action/background IPC lanes.
- Never retry a send after an uncertain result. Passive reads never mark messages read. The adapter never reads or converts whatsapp-web.js auth.
- An empty or partial cache never proves complete history or that a reaction/pin was removed. Do not add unrestricted URL fetching for previews.
- Tests stay offline and synthetic: never open a real account, pair, or touch `auth.sqlite`/QR files.
