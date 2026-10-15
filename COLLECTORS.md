# Telegram collector: подготовка к подключению

Каждый аккаунт подключает владелец в своём терминале. Количество аккаунтов
не ограничено двумя: compose показывает примеры двух независимых WhatsApp и
одного Telegram. Для следующих аккаунтов нужны отдельные configs, sessions,
spools, account IDs и services. Проверки живого сбора относятся к конкретной
установке и не доказывают полноту истории другого пользователя.

## Устройство

- Telethon 1.45.0 закреплён lock-файлами. Обработчики new/edit/delete сначала сохраняют capture в ограниченную очередь с fsync. Недоступная БД не удерживает блокировку записи в очередь. Переполнение очереди/ошибка сохранения останавливает collector и требует reconciliation.
- Процесс держит локальный `flock` session-файла и продлеваемую DB lease. Отдельные leases защищают media jobs; потерявший ownership процесс не может завершить ingest или опубликовать файл. Это не способ удалённо остановить старый Mac-клиент: его остановку и автозапуск проверяют до handover.
- Новый чат получает недавнюю страницу; прежняя история догружается небольшими порциями. Новые сообщения проходят по отдельному high-water cursor из PostgreSQL. Cursor двигается только после записи всей страницы, а live callback его не перескакивает. При падении страницы её captures остаются для replay.
- Повторная сверка недавнего окна обнаруживает правки; persisted tombstones не дают позднему backfill воскресить известное удаление. Удаление без chat ID применяется только к ID-пространству private/basic group, а не к channel с совпавшим номером сообщения.
- Файлы скачиваются по обновлённому provider reference частями по 512 KiB. Размер проверяется до публикации, worker продлевает lease. FloodWait определяет минимальное время следующей попытки; thumbnail не выдаётся вместо оригинала. В0.7.3 явно выбирается PhotoSize/Progressive, размер которого совпадает с descriptor; порядок размеров в ответе не используется как гарантия. Cached/stripped-only preview остаётся unavailable.
- Gateway не получает Telegram credentials. Отдельная collector role имеет DML только для ingest/jobs/positions; ключи агентов, consumer ACK, grants, DDL и изменение collection policy ей недоступны. Эта роль предназначена для доверенного сборщика одного владельца, не является изоляцией между разными владельцами.

Coverage остаётся `partial`: Telegram не гарантирует уведомление обо всех удалениях, недоступная история не равна пустой истории, а ограниченное overlap-окно не доказывает отсутствие старых правок. Исчезновение из недавней страницы не считается удалением. Ограничение discovery и недоступный явно выбранный чат имеют отдельные status codes.

Original downloads use [independent storage admission](MEDIA_STORAGE.md):
`media_disk_reserve_bytes` (default 10 GiB) is separate from the capture/spool
`disk_reserve_bytes` floor. Jobs deferred by local capacity remain queued with
`paused_storage`, preserve their provider attempts, and do not stop new-message
capture. Configure these floors after measuring the host and backup workspace.

### Доступность новых сообщений

Telegram new/edit callbacks и WhatsApp `message_create` (включая исходящие)
сохраняют события в durable spool. Очередь разбирается каждые 250 мс независимо
от периодической сверки истории. Текст и его Postgres full-text vectors становятся
видимыми после одной ingest transaction. Скачивание originals, извлечение текста
и модельная обработка выполняются отдельно и имеют собственные статусы.
250 мс — интервал проверки очереди, не SLA: к нему добавляются доставка провайдера,
fsync, очередь БД и время самого запроса агента. При остановке/reconnect возможны
догоняющие события; `origin=live` не доказывает отсутствие такой задержки.
Stateless MCP не будит простаивающего агента: клиент должен вызвать чтение/updates.

Owner mentions сохраняются по Telegram `mentioned` или совпадению WhatsApp
mentioned IDs с cached phone/LID identities текущего авторизованного аккаунта.
У WhatsApp quoted participant тоже может подтвердить ответ владельцу. Capture
не делает дополнительных contact/quoted-message запросов; неизвестный LID не
сопоставляется с phone по имени или числовому совпадению. Ранее сохранённые reply
IDs можно сопоставить с outgoing только внутри того же account/chat. Это
эвристика очереди внимания, а не provider unread state или факт обязательства.

Документация использованных API: [Telethon catch-up и streaming download](https://docs.telethon.dev/en/stable/modules/client.html), [ограничения MessageDeleted](https://docs.telethon.dev/en/stable/modules/events.html#telethon.events.messagedeleted.MessageDeleted). Реальные Telegram downloads проверены; длительное наблюдение и сценарии reconnect требуют отдельной приёмки. В0.7.2 истёкшие/требующие обновления file references обрабатываются очередью с ограниченными попытками и новой загрузкой metadata, не останавливая сбор остальных чатов.

## Условия перед живым подключением

1. Закончить и проверить автоматический encrypted offsite backup, включая session/spool, и восстановление в изоляции. Сохранить rollback исходного Mac-клиента. Ранее проверенный разовый backup БД/оригиналов этих условий целиком не закрывает.

   Текущий автоматический runner имеет `recovery.accounts: []`, поскольку реальных
   аккаунтов ещё нет. При подключении подготовить RO mounts для session и spool,
   добавить account/session_path и private config/credentials/permit в settings.
   После pairing, пока постоянный collector выключен, выполнить backup и проверить
   восстановление в карантин, не запускать восстановленную сессию. Backup должен
   видеть существующий `.owner.lock`; сам он его не создаёт. После первого запуска
   проверить цикл stop/capture/resume и статус доставки на реальном аккаунте.
2. Зафиксировать точный account и ожидаемый Telegram user ID. Остановить прежний runtime и все пути его автозапуска; проверить отсутствие клиента. Не переносить SQLite session при работающем процессе и не запускать старый MCP параллельно.
3. Оператором применить schema migrations. Выполнить [collector_role.sql](messaging_service/collector_role.sql) и установить отдельный пароль через приватное соединение, без вывода в stdout/argv. Сохранить DSN как `private/collector.dsn`. PostgreSQL не публикуется в интернет.
4. Зарегистрировать account через owner CLI: `register-account --account telegram-primary --platform telegram --label Telegram --discover-chats`. При ограниченном сборе вместо discovery задать `--include-chats`; exclusions задаются отдельно. Регистрация не подключает provider.
5. Подготовить `private/telegram.json`, `telegram.credentials.json`, `telegram.permit.json` по [шаблонам](deploy/telegram.example.json). Значения credentials и expected ID вводятся приватно. Secrets — 0600, доступны только UID сборщика и оператору; новый session directory — 0700. Создать принадлежащие UID10001 каталоги `sessions` и spool volume. Gateway не монтирует их.
6. Только после проверки остановки прежнего клиента поставить `prior_clients_stopped: true`, совпадающие account/expected ID и `enabled: true`. Отсутствие/неверные права секрета, другой user ID или выключенная конфигурация приводят к отказу запуска.

Сессия может быть перенесена после корректной остановки либо создана владельцем заново. Для нового входа предусмотрена команда, запускаемая **самим владельцем в его терминале**, после подготовки выше:

```sh
docker compose -f compose.yaml -f compose.collectors.yaml run --rm --no-deps telegram \
  python -m messaging_service.telegram_pair --config /run/secrets/telegram_config
```

Она требует интерактивный терминал, отдельную DB lease и session lock. Телефон, код и пароль вводятся скрыто непосредственно в терминале. Gateway/MCP этих полей не принимают. Команда проверяет ожидаемый user ID и завершает соединение; постоянный collector сама не запускает. При ошибке identity сбор запрещён, пока оператор не выяснит причину.

Постоянный запуск — отдельный операторский шаг после pairing/проверенного переноса:

```sh
docker compose -f compose.yaml -f compose.collectors.yaml --profile collectors up -d telegram
```

[Compose overlay](deploy/compose.collectors.yaml) не включает collector по умолчанию. У него нет входящих портов; root filesystem read-only, UID10001, dropped capabilities, ограничения CPU/RAM/PIDs. Только этому процессу доступны session, spool и writable originals. Наличие overlay не означает, что он уже запущен.

## Проверка после подключения

Проверить expected identity и account scope, недавний текст/файл, правку и явное удаление, повторный запуск, временную потерю сети/БД, размер/хеш большого оригинала и фактический расход RAM. Сравнить provider IDs в выбранном контрольном окне. Отдельно проверить две независимые consumer ACK и закрытый чужой scope. Тестовые исходящие сообщения владелец может отправить сам; агенту для отправки требуется точное согласование.

Затем проверить 48 часов и отключение прежнего хоста перед заявлением о длительной устойчивости. Каждый следующий аккаунт подключается отдельно, после проверки единственного владельца его сессии. При rollback сначала доказать остановку server collector и сохранить его свежую session/state; затем восстановить единственного владельца на прежнем пути. Оригинальные архивы не удалять.

## WhatsApp adapter

Two supervisors use the same implementation with **different account IDs,
expected WhatsApp IDs, LocalAuth profiles, event/page spool volumes and leases**.
`compose.whatsapp.yaml` is disabled without its explicit profile. Configuration
and handover permit examples start disabled/unconfirmed. Stop and disable the previous client for each account before pairing here.

The Node child runs whatsapp-web.js 1.34.7 with Puppeteer 25.13.0 pinned via an
explicit override (24.38's unused browser downloader dependency chain had audit
findings). `npm ci --ignore-scripts` never downloads a browser; the image uses
Debian Chromium and records its package inventory. Node 24 image is digest pinned
for the current amd64 deployment. Browser/provider compatibility still requires
real account acceptance; importing the modules or passing a blank-page smoke is
not evidence that WhatsApp's current private web modules work.

A private policy file expires after45seconds and is renewed with DB ownership every15seconds, independently from media RPC. Callbacks write allowlisted fields to a private, fsynced, bounded disk queue
before returning. No raw provider object, media key, signed CDN path or QR enters
the archive. Python consumes that queue after checking its hash and schema.
Failed DB writes retain the file. A separate page queue prevents concurrent Node
and Python writers from sharing a publication lock. Changes/deletions are
idempotent, and a deletion arriving before history is a durable tombstone.

WhatsApp IDs are opaque. Backfill and catch-up use persisted `(timestamp, ID)`
boundaries, independently from live callbacks and client ACK. A burst spanning
several pages is reconciled back to the last committed head before advancing it.
Loading at most four provider history batches per call bounds one request; a
cursor not yet reached stays pending, not exhausted. An unavailable bridge to the
old head is reported as a gap. A linked device's exhausted history is always
**partial source coverage**, never proof of a full account archive. Same-second
provider ordering, very deep browser history after restart, old edits/deletions,
media keys and expired links must still be tested with a real account. Status
broadcasts, newsletters and local-only deletion events are outside this adapter's
current supported source types; private/group chats and `@lid` IDs are handled.

Media jobs refresh their reference and stream encrypted HTTPS bytes from the fixed
`mmg.whatsapp.net` host. Redirects, arbitrary URLs/headers and browser `downloadMedia`
are not used. Decryption verifies the MAC, ciphertext hash, original hash and
known length before the blob writer publishes the original. The iterator has
bounded chunks and pipe backpressure; a truncated/cancelled/bad-hash transfer
leaves no `ready` attachment. An expired original is marked unavailable; a
thumbnail never substitutes for it. Protocol reference:
[Baileys media implementation](https://github.com/WhiskeySockets/Baileys/blob/master/src/Utils/messages-media.ts).
Provider API reference:
[whatsapp-web.js Message](https://docs.wwebjs.dev/Message.html) and
[Chat source](https://docs.wwebjs.dev/structures_Chat.js.html).
These source formats are compatibility dependencies, not official WhatsApp APIs.

Owner onboarding, after backup/migration and host sandbox acceptance:

1. Record the expected phone digits as `expected_wid` (`digits@c.us`). Prepare
   separate private files for each account, owned by UID10001 and mode0600;
   session directories are mode0700. Do not reuse one profile for two services.
2. Inventory and stop the old account's one client and its autostart, then record
   its exact account/identity in the permit. Register the real account using the
   operator CLI; install the collector role grants including migration004.
3. Pair from an interactive owner terminal using the matching service:
   `docker compose -f compose.yaml -f compose.whatsapp.yaml -f compose.whatsapp-pair.yaml run --rm --no-deps whatsapp1`.
   The pairing overlay disables Docker logging; QR appears only on terminal stderr. Non-TTY execution is rejected before
   config/session access. The session lock is inherited by the Node child; a
   database lease is renewed through pairing. Wrong identity aborts. Pairing
   does not start the collector.
4. Enable/start **one** matching service, verify live texts/files/reconnect and
   archive identities, then repeat separately for the second account. Keep old
   clients disabled until a verified rollback. Confirmed text sends and allowlisted
   [messenger operations](MESSENGER_OPERATIONS.md) run through this existing
   collector's priority lane under fresh key/account/scope checks. Passive archive
   reads do not invoke this lane or mark provider chats read. Do not start a second
   client against the same profile for agent actions.

Never fix a failing browser sandbox check by adding `--no-sandbox`, privileged
mode or an unconfined container. Resolve the smallest host/container policy
needed and repeat the offline browser smoke before live pairing.


Run the offline sandbox smoke on your deployment host. Required properties:
PID namespaces=Yes, network namespaces=Yes, seccomp-BPF/TSYNC=Yes, UID10001,
no host capabilities, no-new-privileges, read-only root, network disabled and no
account mounts. The custom seccomp profile changes only this container's syscall
filter; host AppArmor/sysctls were not changed. `browser-smoke.mjs` asserts those
sandbox properties before returning success. This smoke test alone does not prove live WhatsApp compatibility.


## Chromium profile restart and cold backup (0.7.4)

Chromium leaves process-local `SingletonLock`, `SingletonSocket`, and
`SingletonCookie` symlinks in its user-data directory. Their targets refer to a
container hostname/PID or temporary socket, not login credentials. Before starting
Node, pairing and collection remove only these symlinks under the account's
exclusive session lock; unexpected regular files and symlinked profile directories
are rejected. Original authentication databases remain intact.

Cold backup omits only these three symlinks in recognized browser profiles within
session sources (`Default` directory and regular `Local State` file). It does not
follow or modify them. Every other special file still fails closed. A restored
profile therefore has no stale container locks. This does not permit concurrent
clients or replace the existing ownership/lease checks.

Reference: [Chromium process singleton implementation](https://chromium.googlesource.com/chromium/src/+/main/chrome/browser/process_singleton_posix.cc).


## Provider compatibility fixes (0.7.5–0.7.9)

Voice-note MIME parameters are normalized to their base type before ingest and
media metadata comparison; control characters remain rejected. A bounded page
load returns `pending` after five seconds instead of terminating all collection
on a stalled chat. Outstanding browser history loads are reused per chat and
capped at four; pending is never evidence of complete history.

Reconciliation enriches sender names from the existing browser cache for the
exact senders in the bounded message page. It does not fetch the contact book or
merge phone/LID identities. Live captures remain durable immediately; missing
names can be filled during reconciliation. See the upstream
[contact fields](https://docs.wwebjs.dev/Contact.html).


## Startup and memory checks (0.7.7–0.7.9)

Provider injection retries only known navigation context loss in the same browser.
Page reads have a Node-side deadline, optional name lookup is bounded, and persisted
last-attempt ordering prevents one failing chat from starving later chats. Cursors
advance only after successful ingest. Media downloads accept fixed-host /v/, /o1/v/
and /o2/v/ paths and still verify ciphertext/MAC/plaintext hashes.

Pairing now waits for initialize and identity verification, confirms a clean Chromium
process exit, then opens the same LocalAuth profile again without allowing another
QR. Success requires both clean closes and the restored identity. The existing
Python 300-second supervisor bounds the entire attempt. No session-saved event or
arbitrary delay is assumed.

Pairing explicitly closes its owned Puppeteer browser before auth-strategy cleanup.
whatsapp-web.js 1.34.7 otherwise skips that close because Puppeteer 25 removed
`isConnected()`. The process-exit check remains mandatory: a timeout, failed close
or abnormal exit cannot start another client or report successful pairing.

The Compose WhatsApp memory cap is 2560 MiB per account. A live test proved that
a 1536 MiB cap could kill the Chromium renderer while Docker OOMKilled remained
false for the container. Inspect kernel/cgroup OOM counters as well as container
status, and size total host memory for every enabled account. This cap does not
prove stable operation for every history size.


## Shutdown during startup (0.7.9)

The stop waiter is active while the provider connects. Shutdown cancels a pending connect, disconnects the owned client, and releases the fenced lease. Timeout and lost-lease errors still propagate. Regression tests cover all three paths; production shutdown must still be verified against the actual browser process.


## Ограниченное окно Telegram (0.8.0 / schema11)

Operator config `history_since` — ISO timestamp с обязательной timezone, либо null для неограниченной истории. Это нижняя граница первоначальной загрузки, а не срок удаления данных: сохранённые сообщения и файлы остаются, новые события продолжают приниматься. Все страницы initial/backfill/reconcile фильтруются по времени сообщения. Старые уже поставленные media jobs остаются в очереди, но не выбираются сборщиком при действующей границе. `history_since` не применяется к WhatsApp.

`max_dialogs: null` включает полный проход по доступному списку через пагинацию Telethon; целое число остаётся явным операторским ограничением. Перед первой загрузкой истории каталог сохраняется в `chats` и `collector_chats`, поэтому пустая история чата не означает отсутствия чата. Политика include/exclude применяется и к каталогу. Повторный обход выбирает давно не предпринятые попытки; попытка сохраняется до API запроса, так что ошибка/restart не заставляет начинать с одного и того же чата.

`window_complete` означает достижение заданной временной границы; `history_exhausted` — конец доступных страниц у провайдера. Это разные признаки. Изменение `history_since` сбрасывает только первый; граница последней страницы сохраняется так, чтобы расширение периода не потеряло отброшенную часть страницы. Ни один из признаков не доказывает полноту старых правок/удалений или доступность всех вложений. Discovery печатает только агрегированные число диалогов, limit и history_since, без названий и сообщений.

Это не новый общий rate governor: существующая обработка FloodWait сохранена. Общий лимит всех API/media запросов и клиентский интерфейс адресного расширения отдельного чата остаются отдельными работами.
