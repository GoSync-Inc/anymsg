# Message formatting proposal

Status: **planned, not implemented**. The current 0.9.13 sender accepts plain
text only. This document adds the formatting feedback to [MESSAGE_ACTIONS.md](MESSAGE_ACTIONS.md);
it does not enable a new send mode or change [SENDING.md](SENDING.md) approvals.

## Contract and implementation order

1. Build a bounded, deterministic renderer without provider calls. Evaluate
   [markdown-it-py](https://markdown-it-py.readthedocs.io/en/latest/) for CommonMark
   token parsing rather than replacing delimiters with regular expressions.
   Explicitly disable raw HTML in Markdown; its default settings enable HTML
   ([security guidance](https://markdown-it-py.readthedocs.io/en/latest/security.html)).
   Choose and pin a version only after testing the required extensions.
2. Add a preview that returns `rendered_text`, structured entities, warnings,
   UTF-16 length, a renderer version and a payload digest. A preview is passive;
   it neither queues a send nor proves owner approval. Distinguish provider wire
   text from visible text where WhatsApp needs native markup.
3. Extend the outbox with an additive migration. Store source text/format,
   provider payload, entities, preview policy and renderer version. Bind
   idempotency to this entire effective payload, account, chat and reply target.
   Dispatch persisted output; do not re-render after a queued job or a restart.
4. Expose `format: plain | markdown | html` in MCP/REST and both clients, keeping
   `plain` as the default. Rich sends must match their reviewed preview digest.
   The digest binds content, not human identity or approval. The acting agent
   still obtains approval for the exact recipient, visible text, formatting,
   warnings, link destinations and reply target before sending.
5. Capture incoming native formatting as versioned evidence. Preserve current
   archive bodies/search behavior; expose formatting separately. Missing capture
   means unknown, not an empty entity list. Formatting-only changes must be
   detectable without rewriting immutable snapshots or hiding conflicts.

These are implementation stages, not currently accepted API parameters. Unknown
`format`/preview arguments continue to fail current strict validation.

## Input dialect

Markdown starts with CommonMark paragraphs, soft/hard breaks, `**bold**`, `_italic_`,
inline/fenced code, block quotes, ordered/unordered lists and links. Enable
strikethrough `~~text~~` and spoiler `||text||` explicitly; they are extensions,
not assumptions about the baseline parser. Preserve literal punctuation such
as `5*3`, prices and unmatched delimiters. Define heading/list rendering and
paragraph breaks in fixtures so profile upgrades cannot silently change sends.

HTML is a **restricted message-markup dialect**, not a web page: allow only
documented text styling, code/pre, quotes, breaks and links. Reject unsupported
tags/attributes rather than executing or fetching them. Scripts, event handlers,
images, embedded media, styles and remote includes are not message formatting.
Limit input length, token/entity count and nesting depth in both modes.

Initial rich-link destinations allow HTTP(S) only, without fetching them during
preview or rendering. Reject executable/local schemes and hidden control
characters. Preserve URL destinations and make them visible in the approval
preview. Provider-native mentions/custom emoji and other special actions require
separate explicit support, rather than constructing them from arbitrary HTML.

## Provider behavior

| Feature | Telegram proposal | WhatsApp proposal |
| --- | --- | --- |
| Bold, italic, strike | Explicit native entities | Native markup with delimiter validation |
| Inline/fenced code | `code`/`pre`, preserving language when supported | Supported native code syntax; warn on unsupported language styling |
| Quotes and lists | Quote entities; textual list markers | Native quotes/list markers after compatibility tests |
| Named links | Text-link entity plus destination in preview | Visible `label: URL`, with a conversion warning |
| Underline | Native underline entity | Unsupported: explicit loss warning before approval |
| Spoiler | Native spoiler entity | Unsupported: reject rich send unless its visible-text conversion was explicitly previewed and accepted |
| Expandable quote | Enable only after installed-client compatibility tests | Unsupported; reject or explicitly preview the fallback |

Telegram sends use explicit entities with `parse_mode=None`. Offsets and lengths
count **UTF-16 code units**, including emoji/surrogate-pair boundaries. The
[Telegram entity specification](https://core.telegram.org/api/entities) also
restricts nesting for some entity types; accepting a parser tree does not prove
that every combination is a valid provider payload. Validate and test the
supported combinations before queuing.

WhatsApp remains on the existing whatsapp-web.js collector and private sending
lane. Do not open another session. Do not claim native markup is disabled by the
current `plain` input mode: it passes text through to the provider. Literal
delimiter behavior needs provider acceptance tests, not invented escaping rules.

Link previews remain off by default. Add an opt-in only with explicit preview
policy in the payload digest and documentation of provider limitations. The
current library documents that its `linkPreview` option has no effect on
multi-device accounts ([send options](https://docs.wwebjs.dev/global.html#MessageSendOptions));
do not promise that a boolean always controls provider behavior.

## Length, evidence and retries

- Keep the current conservative 4000 UTF-16-unit send limit until native payload
  limits are verified for each adapter. Validate the rendered provider payload,
  including any expanded WhatsApp links/markers, rather than source length alone.
- **No automatic splitting.** Return a useful length error and optionally a
  proposed multipart preview. Multipart sending needs its own approved ordered
  payload, per-part idempotency/status and partial-failure semantics first.
- Receipts and identity repair compare the observed provider text with the
  persisted provider payload, not the unrendered Markdown/HTML source. Extend
  agent provenance accordingly; rich text must not break confirmed sender repair.
- Keep source text and render artifacts scoped like the outbox. Status/diagnostic
  responses must not suddenly echo full outgoing bodies, keys or request hashes
  to other readers. An authorized preview deliberately contains its draft.
- Audit identities, action IDs and safe warning codes; do not log draft bodies.
  Quarantine restore cancels queued rich sends and marks interrupted attempts
  unknown, just as it does plain sends. Never retry an uncertain provider result.

## Acceptance

Use synthetic fixtures for Cyrillic, emoji before/inside spans, nested styles,
code containing delimiters, malformed markup, `5*3`, balanced URL parentheses,
lists/quotes, unsafe links/HTML and every unsupported conversion. Test renderer
bounds and UTF-16 offsets, including rejection of split-surrogate offsets.

Then test strict MCP/REST argument parity, installed Python/npm clients,
preview-to-send binding, renderer upgrades/restarts, idempotency conflicts,
ACL/revocation/expiry, reply targeting, source/provenance comparisons,
formatting-only revisions and encrypted backup/quarantine restore. Ordinary
tests must not invoke live providers. Live acceptance uses separately approved
messages for each provider; do not infer visual rendering from mocks alone.
