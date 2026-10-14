# Доставка сервиса

## Этап 1: iCloud pilot

Код локального harness реализован. Live Gate A ожидает подключения Apple Account и server/client проверки. Итог зависит от фактической совместимости; текущие tests используют synthetic fixtures и mock transport.

| Задача | Реализация | Что ещё проверить live |
|---|---|---|
| 1.1 Bootstrap/contract | Package, lock, account-bound Protocol, intent/receipt validation, limits | Не требуется аккаунт |
| 1.2 Onboarding/discovery | Hidden input, encrypted local registry, TLS/redirect/privileges checks | Один аккаунт, затем отдельно второй и shared permissions |
| 1.3 Journal/CRUD | Stable refs, conditional headers, durable read-back, cleanup | CRUD и реальный 412 |
| 1.4 Single/series/roundtrip | Exceptions, alarms, TZ, unknown props, DATE/floating | Сохранение сервером и поведение Apple клиента |
| 1.5 Future/faults/gate | Native probe, split COUNT/UNTIL/infinite, saved plan, compensation/reconcile, checker | Future split и native отдельно; owner visual report |

Fault injection (timeouts, revocation, conflicts) выполняется на mock; не выдаётся за сбои настоящего iCloud. Полный production schema/provider API из проекта не скопирован в пилот: его текущие границы описаны в CONTRACTS.md. Структура файлов объединяет мелкие modules плана, сохраняя соответствующее поведение.

## Следующие этапы

2. PostgreSQL, несколько аккаунтов, группы/семейные роли, calendar и agent grants, production secret store.
3. Google OAuth и прямой Calendar API, два независимых аккаунта, privileges/ETag/recurrence.
4. Incremental sync, source freshness, agenda и общий поиск слотов с полным покрытием обязательных источников.
5. Обязанности dropoff/pickup, previews связанных изменений, durable workers/leases/locks и partial receipts.
6. Единый MCP + REST и интерфейс владельца для подключения, прав и разбора conflicts.
7. Deployment, backup/restore и E2E семейный сценарий Google+iCloud.

Этап 2 требует Gate A core; future включается после своего PASS. Производственная работа агентов с несколькими календарями и автоматические семейные переносы пока не включены.
