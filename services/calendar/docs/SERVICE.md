# Семейный сервис v0.2

## Аккаунт, человек и календарь

`connection_id` — аккаунт провайдера, `person_id` — член семьи, `calendar_ref` — календарь. Календарь ребёнка может храниться в аккаунте родителя: binding учитывает его как источник занятости ребёнка, не меняя владельца аккаунта.

Apple «Календарь» — приложение. Для событий Google в этом приложении нужен Google OAuth; для iCloud — CalDAV. Сервис не копирует события между провайдерами, не исправляет sharing автоматически и не объединяет вручную созданные дубли в один ресурс. Поиск слотов объединяет пересекающиеся интервалы занятости.

## Подключение владельцем

Подключение, роли, grants, write opt-in и ключи доступны только локальному owner CLI. В MCP/REST этих методов нет.

Google: включить Calendar API в своём Cloud проекте, создать desktop/installed OAuth client и сохранить его JSON в приватном файле `0600`:

```sh
uv run family-calendar service connect-google --id google-parent --owner egor --email ACCOUNT_EMAIL --client .local/service/google-client.json
```

Новая ссылка входа временно сохраняется в `.local/service/google-authorization.url`. Открыть её в браузере и подтвердить доступ нужным аккаунтом. Callback слушает loopback до часа; успех показывает `Family Calendar Service connected`. При истёкшем callback начать новый вход. Пароли, коды, callback URL и токены не присылать в чат.

Scopes: OpenID/email, `calendar.calendarlist.readonly`, `calendar.events`, `calendar.app.created`; Gmail/Drive не запрашиваются. Используется новый OAuth flow с state/PKCE/nonce. Проверяются подписанный Google ID token, audience/issuer, verified email и постоянный `sub`. Под тем же connection ID нельзя подменить аккаунт. Чужие существующие refresh tokens не импортируются.

Непроверенное приложение может показать предупреждение. Publishing status конкретного проекта проверяется отдельно. Для External/Testing Calendar refresh token обычно истекает через семь дней; сервис возвращает `reauth_required`, не пустой календарь. [Google OAuth expiration](https://developers.google.com/identity/protocols/oauth2#expiration).

iCloud: сначала скрытый ввод app-specific password в legacy registry, затем импорт и свежий discovery:

```sh
uv run family-calendar connection add-icloud --id apple-parent
uv run family-calendar pilot icloud discover --connection apple-parent
uv run family-calendar service import-icloud --connection apple-parent --owner egor
uv run family-calendar service discover --connection apple-parent
```

Импорт не изменяет credentials, journals и allowlists пилота. Сервис имеет свой Vault и отдельный write opt-in; каждый аккаунт использует отдельный транспорт.

## Семья и разрешения

```sh
uv run family-calendar service member --id partner --label Partner --role adult
uv run family-calendar service member --id child --label Child --role dependent
uv run family-calendar service bind --member child --calendar CALENDAR_REF
```

| Роль | Действия при наличии явных scopes |
|---|---|
| group_admin / coordinator | Разрешённые события и обязанности семьи |
| adult | Разрешённые события и назначение обязанностей себе |
| dependent / helper | Чтение разрешённых источников; запись событий заблокирована |

Администратор не получает доступ к чужим календарям автоматически. Доступ — пересечение scopes ключа, списка календарей агента, grant участника и возможностей провайдера. `busy_only` скрывает названия, описания, места и event/source IDs, блокирует запись.

```sh
uv run family-calendar service grant --member egor --calendar CALENDAR_REF --scopes agenda:read,availability:read,event:read,plan:create,plan:execute,event:create,event:update,event:delete
uv run family-calendar service agent-key --id planner --member egor --calendar CALENDAR_REF --scopes agenda:read,availability:read,event:read,assignment:read,assignment:write,plan:create,plan:execute,event:create,event:update,event:delete --output .local/service/keys/planner.key
```

Для нескольких календарей повторять `--calendar`. Новые календари не добавляются автоматически. Каждому агенту выдаётся отдельный ключ, срок — 90 дней; DB хранит только SHA-256. `revoke-agent --id planner` отзывает ключ и незавершённые планы. Для нового ключа использовать новый principal ID. `disconnect --connection ID` отключает аккаунт и write opt-in; подключать его заново с новым connection ID и явными grants.

Запись по умолчанию выключена. Выбор владельца:

```sh
uv run family-calendar service allow-write --calendar TEST_REF --test-calendar
# Для календаря, явно выбранного владельцем под реальные операции:
uv run family-calendar service allow-write --calendar REVIEWED_REF --reviewed-calendar
uv run family-calendar service disable-write --calendar CALENDAR_REF
```

Write opt-in даёт техническую возможность. Агент должен иметь инструкцию пользователя на конкретное изменение. Тестовые проверки не изменяют обычные события.

## Единый MCP

Официальный Python MCP SDK; Streamable HTTP на `http://127.0.0.1:13232/mcp` с `Authorization: Bearer KEY`. Stdio proxy читает ключ из приватного файла; в конфигурации только его путь. Для Codex использовать абсолютные пути:

```sh
codex mcp add family_calendar -- /ABS/REPO/.venv/bin/python -m family_calendar.service.proxy --url http://127.0.0.1:13232/mcp --key-file /ABS/REPO/.local/service/keys/planner.key
```

Proxy допускает только loopback URL. Новые чаты загружают MCP из конфигурации; уже открытый чат может требовать обновления соединения. Проверка SDK транспорта не доказывает hot reload текущего чата.

| Tool | Назначение / REST method |
|---|---|
| family_capabilities | Возможности и ограничения / capabilities |
| family_calendars | Разрешённые календари / calendars |
| family_members | Члены семьи и роли / members |
| family_agenda | Общая повестка с coverage / agenda |
| family_slots | Свободные интервалы участников / slots |
| family_plan | Сохранённый preview изменений / plan |
| family_apply | Admission с постоянным request_id / apply |
| family_operation | Статус и receipts / operation |
| family_assignments | Просмотр обязанностей в окне / assignments |
| family_assignment | Создание/изменение/отмена обязанности / assignment |

REST: `POST /v1/{method}`, те же JSON arguments, ключ и policy layer. Unknown поля отвергаются; Host/Origin ограничены localhost, body — 64 KiB, notification stream выключен. Gateway не получает Vault и не может изменять DB memberships/grants/keys. Оба процесса работают под одним OS пользователем: это **не изоляция от скомпрометированного процесса того же пользователя**. Deployment требует отдельных OS identities и secret store.

Пример `family_slots`:

```json
{"member_ids":["egor","child"],"start":"2026-10-19T08:00:00+08:00","end":"2026-10-19T20:00:00+08:00","duration_minutes":60,"refresh":true}
```

До 31 дня, 32 календарей, 500 событий. Google pagination и CalDAV REPORT должны завершиться полностью. Coverage старше пяти минут, ошибки и непривязанные участники дают `unknown`. `candidate_slots` относятся только к **настроенным и явно выбранным источникам**; неизвестные календари человека не считаются прочитанными. Неподключённый партнёр не позволяет утверждать доступность всей семьи. Polling выполняется по запросу; это не full background/incremental sync.

`family_members` возвращает `availability_calendar_refs` и `availability_sources_status`
для каждого участника: `configured`, `unbound` или `inaccessible`. Ссылки на недоступные
календари скрыты. Перед поиском слотов сверить источники с инструкцией владельца.
Не включать все видимые календари автоматически: видимость не означает участие в занятости.

`family_slots` принимает необязательный `additional_calendar_refs` — дополнительные явно
выбранные источники. Они объединяются с bindings, а не заменяют их; общий лимит — 32.
Ответ `source_selection` перечисляет итоговые источники и привязки каждого участника.
Дополнительный календарь не устраняет `unbound` другого человека. `verified` относится
только к указанному набору источников, а не к неизвестным календарям или всей семье.

`refresh=true` сначала проверяет весь запрос, затем ставит обновление в очередь.
Одновременные запросы одного агента на то же окно используют существующее обновление.
Ожидание ограничено 20 секундами; ответ содержит `refresh.status` (`pending`, `complete`,
`failed`), безопасные job IDs и `retry_after_seconds`. Для `pending` повторить тот же
запрос с `refresh=false`: это читает состояние без новых заданий. Пока обновление
выполняется, старый кэш не становится подтверждением свободного времени. Сбой провайдера
отражается в coverage даже при отсутствии предыдущего кэша.

`family_capabilities.constraints` описывает ограничения записи, свежести, размеров окна
и планов. `calendars[].allowed_event_actions` учитывает текущие scopes, роль, видимость,
права провайдера и write opt-in. Отдельный `write_enabled` — только настройка календаря;
он сам по себе не разрешает агенту запись. В общем MCP используются имена `calendar_*`;
шлюз дополнительно ограничивает действия выбранными инструментами и grants.

## Изменения и восстановление

Читать capabilities/agenda, создать `family_plan` на 1–8 действий, проверить preview в пределах инструкции пользователя, вызвать apply с сохранённым `request_id`, читать operation. План живёт десять минут. Права перепроверяются перед admission и отправкой. Update/delete используют `event_ref` из повестки и сохранённый ETag; worker свежим GET проверяет версию.

```json
{"actions":[{"action":"create","calendar_ref":"CAL_REF","event":{"title":"Lesson","start":"2026-10-19T16:00:00+08:00","end":"2026-10-19T17:00:00+08:00","timezone":"Asia/Makassar"}}]}
```

```json
{"plan_id":"PLAN_REF","request_id":"stable-user-request-001"}
```

Время события в `plan` — ISO 8601 со смещением или `Z`; без смещения (`YYYY-MM-DDTHH:MM[:SS]`) оно читается как местное время в `event.timezone` (IANA). Несуществующее и двойное местное время перехода DST отклоняются как `invalid_local_time` и `ambiguous_local_time`. Окна agenda/slots/assignments требуют смещения. Ошибки времени содержат фиксированный `hint`, а `capabilities.constraints.time_format` описывает формат. `plan` сохраняет только локальный план и не меняет календари, поэтому помечен как недеструктивный; чтения помечены read-only.

Intent и `sending` сохраняются до сети; operation/resource advisory locks защищают от двух workers. После записи обязателен read-back. Повтор apply с тем же request ID возвращает ту же operation; другой plan под этим ID отклоняется. Crash/unknown сверяется по тому же Google ID или CalDAV href и **не отправляет create повторно**. `service reconcile --operation OP_REF` проверяет отправленные неизвестные шаги; если результат не доказан, остаётся `needs_attention`. После ambiguous шага следующие не отправляются. Между провайдерами нет атомарности; partial не означает rollback.

CalDAV create: `If-None-Match`, update/delete: `If-Match`. Google update/delete: `If-Match`; insert: сохранённый client-generated ID, поскольку conditional insert не поддерживается. [Google resource versions](https://developers.google.com/workspace/calendar/api/guides/version-resources).

Сейчас запись — отдельные неповторяющиеся timed события без гостей/специальных Google типов. Незапрошенные поля и ICS свойства/alarms сохраняются. Recurrence/future из harness не включены в MCP. Обязанности `dropoff/pickup/stay_with_child` хранятся отдельно: assigned занимает слот, proposed — нет. Изменять/отменять по `assignment_id` и `expected_version`. Согласие другого человека не утверждается. Автоматической связи с занятием ребёнка и проекций в календари родителей пока нет.

## Эксплуатация

`family-calendar-local status/start/stop` управляет только собственными macOS launch agents; PostgreSQL остаётся отдельным persistent container. Docker daemon должен работать. В argv/env не передаются ключи/пароли; логи приватны. На других ОС запускать вручную либо под своим supervisor.

Credentials и raw source cache зашифрованы, но event details, plans и journal содержат приватные данные в DB. Volume этим сервисом не шифруется. Backup должен включать PostgreSQL, Vault/key, приватные DB профили и ключи агентов. Автоматический backup и restore ещё не установлены/не проверены. Это локальный MVP, не публичный multi-tenant deployment.

## Границы доступности и предпросмотра

`slots` перечисляет `checked_member_sources` и итоговый `source_selection`.
`availability_scope=configured_bindings` при одних bindings; дополнительные явно
выбранные источники дают `configured_and_explicit_sources`.
Binding определяет проверенный источник, но не подтверждает, что перечислены все
календари человека. Поэтому общая `availability` остаётся `unknown`,
`member_sources_complete=unknown`, а `required_sources_complete=false`. При свежем
покрытии всех настроенных источников `configured_bindings_availability=verified`,
`configured_sources_complete=true`, и `candidate_slots` содержит свободные интервалы
только внутри итогового `source_selection` (bindings плюс дополнительные источники).
При pending/failed refresh эти поля сбрасываются в unknown/false/пустой список.
`verified_slots` остаётся пустым. Подключение аккаунта
само по себе не создаёт binding и не расширяет grants.

Предпросмотр `plan` возвращает изменения полей `diff` и `overlap_check`: полуоткрытые
интервалы занятости в доступных для чтения календарях, включая busy-only без названий
и идентификаторов событий. Переносимые/удаляемые ресурсы исключаются из старого
расписания; пересечения между предложенными шагами показываются отдельно. Отсутствие
или просроченность покрытия помечает проверку `incomplete`. Это снимок кэша, не блокировка
времени и не гарантия отсутствия параллельных правок при выполнении.

Календари сохраняют отдельную идентичность ресурса провайдера (`identity_basis`).
`primary` и `duplicate_of` равны null до явного определения владельцем: одинаковое
название или другой часовой пояс не доказывают, что это дубли.
