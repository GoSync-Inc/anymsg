# Доступ агентов и аудит

Messaging 0.5 / schema5. Настройка выполняется оператором через локальный CLI
или SSH с owner DSN. Удалённых MCP/REST методов управления ключами и правами нет.
Provider sessions, owner DSN и ключ расшифровки backup агентам не выдаются.

## Что разрешает credential

Каждый агент имеет свой principal и отдельный Bearer key. Ключ действует
1–30 дней (по умолчанию 7), хранится в БД только как SHA256, имеет публичный UUID
для отзыва. Несколько ключей одного principal имеют общие права и ACK; разные
агенты получают разные principals. Ключи нельзя передавать в query string,
вставлять в переписку или сохранять в отслеживаемые Git файлы.

| Уровень | Правило |
|---|---|
| Аккаунты | Явный непустой список существующих account IDs |
| Чаты | `NULL`/`--all-chats` — все чаты этих аккаунтов, включая будущие; `--chats ID…` — только перечисленные; пустой `--chats` — ни одного |
| `read` | Поиск, история, контекст с метаданными вложений, список чатов, чтение событий |
| `download` | Получение оригинала/статуса вложения и временной ссылки; известно разрешённое attachment ID |
| `cursor-write` | ACK ранее полученной страницы; сам по себе не разрешает читать архив |
| `review-write` вместе с `read` | Неизменяемая отметка разбора с автором, ревизиями всех источников и ссылкой на результат |
| `send` | Enqueue exact owner-confirmed text and inspect own send receipts; requires separate account opt-in (SENDING.md) |
| `capabilities` | Доступен любому действующему ключу, в том числе без операционных разрешений |

По умолчанию новый principal получает read/download/cursor-write в явно заданных аккаунтах.
`review-write` выдаётся отдельно, только вместе с `read` позволяет создавать отметки;
чтение отметок входит в `read`. Подробности: [REVIEWS.md](REVIEWS.md).
Для ограниченного помощника указывайте chat IDs и permissions при выдаче.
`read` включает имена/метаданные файлов в контексте сообщения, но без `download`
оригинал и ссылка не выдаются. `download` является отдельным правом на содержимое
файла; это следует учитывать, выдавая его без `read`.

`capabilities` возвращает текущие права, `scope_version`, публичный `credential_id`
и разрешённые accounts. При ограничении по чатам account-wide timestamps/status
и причины деградации скрыты (`coverage=chat_scope_only`): они могут зависеть
от чужой переписки. Пустой список чатов не раскрывает даже labels аккаунтов.

## Операторские команды

Команды требуют `DATABASE_URL_FILE` с owner DSN. Ниже placeholders, не настоящие
account/chat IDs. Выходной key-файл создаётся с режимом0600 и не перезаписывается.

```sh
python -m messaging_service issue-key --principal assistant-work \
  --accounts account-work --chats chat-work --permissions read download cursor-write \
  --days 7 --key-file /private/new-assistant-work.token

python -m messaging_service set-access --principal assistant-work \
  --chats chat-work --permissions read

python -m messaging_service list-keys --principal assistant-work
python -m messaging_service revoke-key --key-id PUBLIC_CREDENTIAL_UUID
python -m messaging_service set-access --principal assistant-work --disable
python -m messaging_service set-access --principal assistant-work --enable
python -m messaging_service audit-tail --principal assistant-work --limit 50
```

Неуказанные права сохраняются. Повторная выдача ключа тому же principal
без `--chats`/`--permissions` сохраняет его ограничения; account list указывается
явно. Ротация с теми же правами не сбрасывает ACK. Выдача ключа отключённому
principal не включает его обратно.

Любое изменение аккаунтов, чатов, операций или enabled увеличивает scope version:
старые receipts и download grants перестают работать. Тот же consumer начинает
читать события заново в новом scope. Повторное включение/возврат прежних прав
не оживляет прежние receipts. Уже переданный клиенту текст или файл отозвать
невозможно. In-flight операция заканчивается до изменения прав; следующая
операция перепроверяет их в БД. Все operator mutations используют ту же
транзакционную блокировку principal; прямой UPDATE в обход CLI нарушает это правило.

## Границы журнала

`audit_log` содержит время БД, principal, публичный key UUID, db_role, actor,
фиксированные operation/outcome/error codes и максимум100 ID уже разрешённых
ресурсов. Нет тела сообщения, поискового запроса, credentials, download grant,
URL, неизвестного переданного ID или traceback. Отклонённые запросы имеют пустой
список ресурсов. Operator mutations отмечены отдельно.

Успешное изменение ACK/выдача ссылки и его audit коммитятся атомарно. Если запись
аудита не удалась, операция возвращает ошибку и откатывается. Отклонённая операция
откатывает свои записи, но сохраняет audit отказа. На HTTP boundary неавторизованные
и некорректные запросы учитываются анонимно с лимитом5 записей/min/peer, чтобы
не заполнить БД посторонним трафиком. SDK/protocol validation до вызова Store
не является полноценным журналом обращений: это не forensic access log каждого
сетевого пакета. SDK ошибки возвращаются через `ToolError` без crash traceback.
Дополнительно0.5.1 редактирует диагностические записи MCP/HTTP/DB-pool библиотек
до форматирования: остаются severity, фиксированная component category и тип
исключения. Исходный message/args/traceback убираются. Это требуется даже для
ожидаемых SDK warnings: неизвестное имя инструмента может быть входным текстом.
Ошибку с подробным traceback следует воспроизводить на вымышленных fixtures,
не включать verbose logging реальной переписки на публичном gateway.

Gateway DB role `messaging_gateway` имеет SELECT архива, CRUD собственных
служебных таблиц consumers/deliveries/grants и только column INSERT аудита.
Он не может читать, редактировать или удалять audit, подменять actor/db_role/time,
менять ACL/ключи, выполнять DDL или управлять сборщиками. Owner применяет
`messaging_service/gateway_role.sql`, затем выдаёт membership выделенной login role.
Account/chat ACL проверяет общий слой Store для MCP и REST; это не PostgreSQL RLS.
Компрометация самого gateway процесса — отдельная граница угроз, поскольку его
DB role читает архив. Агенту этот DSN не доступен.

Retention аудита пока не включён: строки сохраняются и входят в зашифрованный
backup. До существенной нагрузки нужны измерение роста и согласованный срок
хранения. Gateway/uvicorn access logs выключены. Прокси/клиенты имеют отдельные
журналы; их redaction проверяется при deployment и перед живым переносом.
В [примере Caddy](deploy/Caddyfile.example) глобальный log encoder
удаляет весь блок request и response headers, включая error logs при502:
одного отключения access log недостаточно, URI может содержать download grant.
Logger/status/duration сохраняются. Это настройка журналирования, маршруты
запросов не меняются. Поддержку проверять на фактическом proxy binary через
`caddy validate` и изолированный failed-upstream test; справка:
[global log](https://caddyserver.com/docs/caddyfile/options#log),
[filter encoder](https://caddyserver.com/docs/caddyfile/directives/log#filter).

## Upgrade и восстановление

Migration005 сохраняет существующие аккаунты, ключи, сроки, ACK и scope versions;
им назначаются прежние возможности read/download/ACK во всех разрешённых чатах.
Backup image с поддержкой schema5 должен быть установлен **до** миграции:
предыдущий image знает только schema≤4. Schema5 включает23 таблицы, в том числе audit.

**Не откатывать один API image на0.4 после применения ограничений.** Он не знает
chat/operation/enabled policy и может выдать данные по прежним широким правилам.
При неисправности остановить gateway, исправить0.5 либо восстановить отдельно
проверенный snapshot в quarantine с отозванными ключами и отключёнными workers.
Не открывать восстановленный архив старым ключам до повторного назначения прав.

Migration006 добавляет опциональный review-write, не расширяя существующие права.
Backup image0.6 должен предшествовать schema6; manifest включает26 таблиц.
Gateway имеет SELECT и ограниченный column INSERT reviews/review_sources,
SELECT/UPDATE счётчика публикации, но не UPDATE/DELETE отметок или времени создания.

## Opt-in sending (schema22)

See [SENDING.md](SENDING.md). Migration does not grant send to existing keys.
Gateway can only insert request columns/read outbox; collector alone updates
states and reads public credential expiration/revocation and ACL metadata,
without token hashes. There is no remote access administration or server approval
gate. Existing agent-side exact owner confirmation remains mandatory.
