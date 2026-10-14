# Pilot contract v1

`family_calendar/contracts.py` фиксирует `EventRef(connection_id, calendar_id, resource_id, occurrence_key)`, `Scope=single|future|series` и `WriteReceipt`. Verified receipt требует evidence hash; для подтверждённого удаления используется `absent`. Журнал хранит более подробное внутреннее представление шага, включая payload и фактический read-back, и не является публичным агентским API.

`providers/base.py` — account-bound Protocol текущего пилота: `discover`, `read`, `read_window`, conditional `put` и `delete`. Adapter создаётся с фиксированным connection ID и отдельным транспортом. Credentials не выбираются через глобальный «текущий аккаунт».

`Journal.prepare` проверяет mutation intent до сохранения: допустимы create/update/delete; для update/delete обязателен сильный ETag. Executor проверяет принадлежность href выбранной collection и локальный write grant перед новым внешним шагом. Производственный opaque-ref API, sync и compile-change будут расширять эту границу позднее.

## Ограничения

| Значение | Pilot v1 |
|---|---|
| Python | 3.13 |
| ICS/HTTP response | максимум 1 MiB |
| Expansion | максимум 10 000 экземпляров |
| Создаваемые объекты одного run | максимум 16 |
| Timezone fixtures | Asia/Makassar, Europe/Berlin, UTC |
| VTIMEZONE генерация fixtures | 2020–2035 |
| Future | simple weekly, COUNT/UNTIL/infinite, preserve_absolute |
| Network | HTTPS iCloud hosts, проверка сертификатов, без env proxies |
| Mutation retries | отсутствуют; unknown → reconciliation |
| Invitations/sharing | не поддержаны |
| Concurrency | один оператор/процесс на run |

`read_window` выдаёт `complete_window` только после корректного CalDAV multistatus с полными ресурсами. 401/403, malformed XML, недостающий calendar-data и лимит ответа — ошибки; не пустой календарь. `series_inventory_complete=false`: это не full sync и не доказательство свободного времени всей семьи.

## Journal и evidence

Состояния шага: prepared → sending → verified / conflict / unknown / failed / needs_attention. Intent сохраняется транзакцией с synchronous=FULL до HTTP; request ID и payload hash проверяются при повторе. Verified сохраняет реальные read-back bytes, ETag и semantic hash. Unknown/sending не повторяет mutation, а сверяет тот же href.

Expected stale-version 412 отличается от произвольного conflict. После критического auth/permission/grant сбоя матрица не начинает следующие mutations. Длительные settling windows не угадываются: неопределённость остаётся needs_attention и оператор может повторить reconciliation позже.

Report v1 содержит scope, run/environment, runtime_versions, checks → step_ids, компактные operation receipts, capabilities, generated_at/evidence_as_of и Gate A. До реального server+client PASS capabilities остаются unverified; mocks не принимаются. Проверяющий скрипт сверяет сохранённые bytes и hashes локально; он не заменяет live/client evidence и не предотвращает намеренную подделку владельцем всех файлов.

Перечень публичных ошибок задаётся фиксированными кодами, например `reauth_required`, `provider_forbidden`, `version_conflict`, `write_unknown`, `conditional_write_unavailable`, `write_not_enabled`, `unsupported_recurrence`, `invalid_local_time`, `ambiguous_local_time`, `floating_time_requires_timezone`, `run_scope_mismatch`. Тексты provider exceptions, credentials и HTTP bodies в CLI errors не выводятся.

Файловые форматы v1 ещё не выпущены для боевого сервиса. Миграция ранних экспериментальных SQLite/config схем автоматически не выполняется. PostgreSQL migrations и worker fencing входят в следующий этап.
