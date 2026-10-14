# Family Calendar Service

Отдельный сервис для агентского управления несколькими семейными календарями. Первый реализованный этап — локальный пилот **iCloud CalDAV**. Он проверяет подключение, условную запись, повторяющиеся события и восстановление после неопределённого результата.

Пилот готов к подключению аккаунта. Совместимость с настоящим iCloud и Apple «Календарём» должна быть подтверждена live-проходом; mock-тесты не дают такой гарантии. Google API, PostgreSQL, семейные роли, обязанности отвозит/забирает и единый MCP идут следующими этапами.

## Подключение

Создайте отдельный пустой календарь **Family Calendar Pilot** в iCloud и пароль приложения для этого сервиса. Затем из корня репозитория:

```sh
uv sync --locked
uv run family-calendar connection add-icloud --id pilot-a
uv run family-calendar pilot icloud discover --connection pilot-a
```

Первая команда подключения спрашивает email Apple Account и скрыто вводимый **app-specific password**. Вторая только читает список календарей. Запись включается отдельно для конкретного `calendar-ref`.

Полная пошаговая инструкция, запуск матрицы, визуальная проверка и очистка: [docs/ICLOUD_PILOT.md](docs/ICLOUD_PILOT.md).

## Что реализовано

- Независимые connection ID и зашифрованные credentials; отдельный HTTP-клиент на аккаунт.
- HTTPS CalDAV discovery, privileges, чтение окна, запрет передачи credentials на чужие хосты.
- Create/update/delete с `If-None-Match` / сильным `If-Match`, read-back и сохранением фактического ответа.
- SQLite journal до отправки, стабильные UID/href, reconciliation вместо повтора неизвестной записи.
- Single move/cancel, series shift, future split простой weekly серии с COUNT/UNTIL/без конца.
- Сохранение exceptions, EXDATE/RDATE, VALARM, VTIMEZONE, X-свойств и Unicode.
- Приватный отчёт с версиями библиотек, capability matrix и проверяемыми receipts.
- Ограниченная очистка только собственных неизменённых тестовых объектов.

Это локальный harness одного оператора. Он не обслуживает агентов через MCP и не предназначен для боевых семейных расписаний до live-приёмки и следующих этапов.

## Разработка

Нужен `uv`; Python 3.13 выбирается окружением проекта. Версии зафиксированы в `uv.lock`.

```sh
uv sync --locked
uv run pytest tests/unit tests/contract
uv run ruff check .
uv run ruff format --check .
uv build
```

Обычные тесты работают на вымышленных ICS и mock CalDAV, без Apple аккаунтов и внешней сети. Live запуск выполняется только через явный CLI opt-in.

Документы: [контракт](CONTRACTS.md), [повторы и ограничения](docs/RECURRENCE.md), [выбор транспорта](docs/ADR-001-CALDAV.md), [этапы](docs/ROADMAP.md), [лицензии зависимостей](THIRD_PARTY.md). Код распространяется по [MIT](LICENSE).
