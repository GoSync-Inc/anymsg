# Family Calendar Service

Общий бэклог, приоритеты и статусы: **[DeedKit BACKLOG](../../BACKLOG.md)**.

Отдельный локальный сервис для агентского управления несколькими **Google Calendar и iCloud CalDAV** аккаунтами. Версия 0.2.0 включает PostgreSQL 18, семейные роли, явные grants, обязанности, общую повестку/слоты и единый MCP/REST. Создание, перенос и удаление отдельных событий проверены live на тестовых календарях обоих провайдеров.

Подключение, запуск, роли, ключи агентов и примеры: **[docs/SERVICE.md](docs/SERVICE.md)**. Повторяющиеся события читаются; их изменение, приглашения, sharing и связанные семейные проекции пока не включены в MCP. Исходный iCloud harness ниже сохранён независимо от сервиса.

## Запуск сервиса

Нужны `uv`, Python 3.13 и работающий Docker. Из корня репозитория:

```sh
uv sync --locked
uv run family-calendar-local bootstrap
uv run family-calendar service init-family
# Постоянный запуск на macOS:
uv run family-calendar-local install
uv run family-calendar-local status
```

Gateway слушает `127.0.0.1:13232` (`/health`, `/mcp`, `/v1/{method}`). Credentials и настройки находятся в игнорируемом `.local/`; не добавлять их в Git. Docker daemon должен работать; его запуск после логина настраивается отдельно.

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

Это возможности отдельного legacy harness. Его recurrence/future операции не распространяются автоматически на сервисный MCP.

## Разработка

Нужен `uv`; Python 3.13 выбирается окружением проекта. Версии зафиксированы в `uv.lock`.

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

Обычные тесты работают на вымышленных ICS, mock провайдерах и изолированных схемах PostgreSQL, без реальных аккаунтов и внешней сети. DB тестам нужен локальный bootstrap либо `FCS_TEST_DSN`; без него они пропускаются, и такой проход нельзя считать полной проверкой. CI предоставляет PostgreSQL 18. Live запись требует явно разрешённого тестового календаря.

Документы: [контракт](CONTRACTS.md), [повторы и ограничения](docs/RECURRENCE.md), [выбор транспорта](docs/ADR-001-CALDAV.md), [этапы](docs/ROADMAP.md), [лицензии зависимостей](THIRD_PARTY.md). Код распространяется по [MIT](LICENSE).
