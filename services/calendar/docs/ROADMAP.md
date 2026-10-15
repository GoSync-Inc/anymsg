# Доставка сервиса

Текущая очередь и статусы ведутся только в **[общем BACKLOG](../../../BACKLOG.md)**
(DK-15–DK-19). Этот документ сохраняет историю этапов и границы пилота.

## Текущий MVP v0.2

Реализованы Google/iCloud connections, PostgreSQL, family/agent grants, роли, bindings, обязанности с версиями, agenda/slots, durable workers/leases/locks, MCP/REST и локальный owner CLI. Live CRUD отдельных событий проверен на тестовых календарях Google и iCloud. Исходный iCloud core/future pilot имеет отдельные приватные receipts и визуальные наблюдения.

Это часть этапов 2–6, а не принятие всех gates. Оставшаяся реализация и отдельные
проверки нескольких аккаунтов/deployment/restore перечислены в общем бэклоге.
Обновление источников — bounded polling; unknown не считается свободным временем.
[Текущий контракт](SERVICE.md).

## Этап 1: iCloud pilot

Код локального harness реализован; матрица ниже сохраняется как история этапа. Live результаты находятся в приватных отчётах владельца; normal tests используют synthetic fixtures и mock transport.

| Задача | Реализация | Что ещё проверить live |
|---|---|---|
| 1.1 Bootstrap/contract | Package, lock, account-bound Protocol, intent/receipt validation, limits | Не требуется аккаунт |
| 1.2 Onboarding/discovery | Hidden input, encrypted local registry, TLS/redirect/privileges checks | Один аккаунт, затем отдельно второй и shared permissions |
| 1.3 Journal/CRUD | Stable refs, conditional headers, durable read-back, cleanup | CRUD и реальный 412 |
| 1.4 Single/series/roundtrip | Exceptions, alarms, TZ, unknown props, DATE/floating | Сохранение сервером и поведение Apple клиента |
| 1.5 Future/faults/gate | Native probe, split COUNT/UNTIL/infinite, saved plan, compensation/reconcile, checker | Future split и native отдельно; owner visual report |

Fault injection (timeouts, revocation, conflicts) выполняется на mock; не выдаётся за сбои настоящего iCloud. Полный production schema/provider API из проекта не скопирован в пилот: его текущие границы описаны в CONTRACTS.md. Структура файлов объединяет мелкие modules плана, сохраняя соответствующее поведение.

## Границы доставленного MVP

PostgreSQL, Google OAuth/API, несколько connections, базовые семейные роли/grants,
agenda/slots, обязанности, durable workers и MCP/REST уже реализованы. Перечень
старых этапов 2–7 не является списком ещё не написанных возможностей. Серверные
deployment templates включены в платформу; доказательства конкретного переноса
и восстановления учитываются отдельно от их наличия в репозитории.

Future включается после своего PASS. Несколько календарей уже доступны через локальный MCP в явной области разрешений; автоматические переносы связанных семейных занятий и проекций пока не включены.
