# Поиск и чтение архива

AnyMsg хранит сообщения и файлы зарегистрированных аккаунтов и отвечает агентам.
Поддерживаются Telegram и WhatsApp; наличие адаптера не означает, что аккаунт
подключён или его история полна. Состояния покрытия возвращаются в ответах.

`search` принимает `accounts`, `chats`, `senders` — списки до100 точных ID.
Внутри списка действует «или», между списками и прежними одиночными фильтрами —
«и». Все условия пересекаются с правами ключа. `null`/отсутствие не ограничивает
измерение, `[]` возвращает пустую выдачу. Чужой ID в списке не расширяет права.
Sender ID относится к источнику/аккаунту; совпадение строк не объединяет людей.

`query_mode`:

- `all` по умолчанию: все слова в любом порядке;
- `phrase`: слова в указанном порядке как фраза;
- `web`: кавычки, `OR`, исключение `-слово`, например `"срок оплаты" OR invoice`.

Поиск использует встроенные словари PostgreSQL для словоформ; языки, точный
поиск и явный approximate fallback описаны ниже. Синонимы не добавляются автоматически. `search` индексирует текст сообщений;
для имён есть [search_people](PEOPLE.md), для содержимого и имён файлов —
[search_files и get_file_text](FILE_INDEX.md). Пустой
результат не доказывает отсутствие сообщения у провайдера. Смотрите coverage.

`from_time` включается, `to_time` не включается; даты с timezone приводятся кUTC.
`direction` ограничивает incoming/outgoing. Порядок `asc` по умолчанию,
`desc` начинает с новых. `next_cursor` передаётся как `after` при неизменных
фильтрах и порядке; это позиция живого архива, не зафиксированный snapshot.
Изменения источника между страницами видны отдельно через `get_updates`.

Раскрытие результата:

1. `get_context(message_id)` возвращает исходное сообщение, соседние записи,
   attachments и `context_window`. Два ограниченных прохода по индексу заменяют
   сортировку всей переписки; сообщение-якорь включено, если не удалено.
2. Старше окна: `get_messages(chat_id, before=older_cursor, order="desc")`.
3. Новее окна: `get_messages(chat_id, after=newer_cursor, order="asc")`.
4. Следующие страницы — `after=next_cursor` с тем же порядком. `after` и `before`
   одновременно запрещены. Одинаковые timestamps разделяются стабильным ID.
5. `get_attachment(attachment_id)` выдаёт сведения и временную ссылку на
   оригинал; pending/unavailable не подменяются превью. Файл не помещается вJSON.

Поиск, контекст и история не обращаются кTelegram/WhatsApp и не двигают ACK.
MCP и REST используют один Store/policy layer. По имени сначала вызывается
`search_people`, затем сообщения ищутся по возвращённым account/sender_id.
По файлам `search_files` даёт attachment_id, source message и статус индексации;
`get_file_text` возвращает текст частями с SHA256 оригинала и ревизией источника.

## 0.9 query options

`search_language` accepts `auto`, `simple`, `russian`, `english`, `indonesian`.
`auto` tries complete queries under matching PostgreSQL dictionaries; it does
not detect a message's language. `query_mode: exact` means case-insensitive
literal substring, including punctuation and identifiers. Existing `all`,
`phrase`, `web` modes and chronological `asc`/`desc` remain available.

`order: relevance` returns an opaque query/scope-bound `next_cursor`; pass it
unchanged as `after`. Editing the anchor can invalidate it. `fuzzy: true` is
an explicit fallback for a single word of 3–64 characters, used only when the
scoped primary query has no matches, with at most 200 ranked candidates.
`match_type` distinguishes morphology, literal and approximate matches.

`compact: true` omits full message bodies while retaining snippets and identity.
`body_chars` bounds returned bodies; `include_coverage: false` suppresses the
verbose source coverage block without changing permissions. These options are
also available for history/context. Context accepts separate `before_count`
and `after_count`, always preserving the authorized anchor.

Message metadata includes observed sender name/username, chat title/type,
direction, and known reply/forward/media fields. Unknown metadata remains null.
Older messages acquire provider metadata as they are observed again; this does
not invent a new content revision.

`list_chats` defaults to stable ID order for old clients; `order: activity`
returns recently active chats first. Account/type/date filters and
`exclude_channels` are supported. `awaiting_reply` is only a heuristic from the
last known incoming message in a private chat, plus explicit attention in groups:
the provider marks an owner mention, the cached quoted participant is the owner,
or the quoted message is an archived outgoing message in the same account/chat.
Group size is irrelevant to an explicit mention/reply. Ordinary group chatter and
channels are excluded. Later messages by others do not clear the hint; a later
owner message does, heuristically. This does not prove a request was fulfilled.
The result supplies `awaiting_reply_message_id`, `awaiting_reply_basis` and a
snippet of the specific group message; last chat activity remains separate.
Old messages without captured mention metadata are not assumed to have none.
`group_attention_coverage=observed_mentions_and_replies_only` states this limit;
later reconciliation can acquire missing provider metadata. Reading the queue
does not alter provider unread state or mark a request completed.
Unread/muted/archived values remain unknown when the provider has not supplied
reliable evidence.

`get_updates` supports filters by accounts, chats, senders, origins, kinds,
direction and dates. A consumer name binds its initial filter permanently.
Choose a new consumer name for a different view; this prevents a filtered ACK
from skipping events intended for another view. ACK order remains commit order,
not the original timestamp of a backfilled message.


## Coverage and diagnostics

Version0.9.2 adds result_evidence, per-chat sync evidence and exact provider ID resolution. Inspect `result_evidence` and call `get_sync_status(chat_id=...)` before interpreting empty or partial results. The archive never substitutes a search error with no matches.

## Query execution and timing

Version0.9.3 separates indexed text matching from chronological page selection.
Rare and absent queries must not walk the entire archive through a date index.
File search first selects candidate IDs from document/passage indexes, then
applies the existing ACL, current-source and best-passage checks. History with
an empty query retains its bounded chronological index path.

Measure database execution, direct REST/MCP HTTP and the full client connector
separately. A fast REST call does not measure connector initialization, retries
or client processing. Global `get_sync_status` and file coverage counts examine
the permitted scope and can take longer than a specific chat read. Prefer
`get_sync_status(chat_id=...)` when investigating one chat; for message/chat
lists, compact output and optional coverage reduce the returned payload.
