# Проверка реализации 2026-10-14

## Сервис v0.2 — 2026-10-15

- Полный normal прогон: **113 passed**, без skips, с настоящим PostgreSQL 18 и изолированными test schemas. Один warning из Starlette/AnyIO об устаревшем alias; он не является ошибкой тестов.
- Policy tests: grant intersection, busy-only redaction, неизвестная занятость, scope revocation, group isolation, версии обязанностей и отсутствие ложного подтверждения другим человеком.
- Worker tests: unknown без повторного create, crash reconciliation, read-back auth failure, два workers, сериализация записи/публикации read snapshot, доступ к receipts после выключения write opt-in.
- Vault/OAuth identity, provider pagination, conditional headers, сохранение идентичности события при переносе, floating-time degradation, REST/MCP schema и origin checks.
- Live через официальный MCP SDK: один Google и два iCloud connection читаются независимо. В двух тестовых календарях create → update → delete: **6 verified mutations**, стабильные refs и read-back; тестовые события удалены. Нормальные календари не изменялись.
- Stdio MCP из другого project cwd: 10 tools, 32 разрешённых календаря, без provider IDs/credentials и без write opt-in. Повтор apply с тем же request ID возвращал существующую operation.
- Slots live: configured parent+child sources complete; добавление непривязанного партнёра даёт unknown, без verified slots. Доступ только к занятости не открывает agenda details.
- Отдельные DB роли не могут менять grants/keys/memberships/connections/write opt-in. Оба процесса имеют одного OS пользователя; это не production process isolation.
- Private live receipts сохранены вне Git. Первый пробный conditional Google insert сохранён как остановленная неопределённая операция, не пересылался; read-back не нашёл объект. Успешная матрица — отдельный тест после исправления provider-specific precondition. История не переписана в PASS.

MCP зарегистрирован локально; уже открытые Codex чаты не проверены на hot reload. Не проверены второй независимый Google аккаунт, sharing супругам, Google recurrence write, связанные семейные проекции, incremental sync и backup/restore. CI конфигурация обновлена, remote CI и публикация не запускались. Исходный статус этапа 1 ниже сохранён как история.

Реализован первый этап локального iCloud pilot. Live Gate A **не принят**: настоящий Apple Account ещё не подключён, server/client совместимость не проверена.

Проверено локально на Python 3.13.11 / uv 0.9.26:

- `uv run pytest tests/unit tests/contract`: **53 passed**.
- Ruff check и format check: PASS.
- `uv lock --check`: PASS.
- Source distribution и wheel успешно собраны.
- Установка wheel в изолированное uv окружение и `family-calendar --help`: PASS.
- Артефакты проверены на отсутствие `.local`, `outputs`, `.venv`, private keys и journals; ссылки документов проверены.
- Git ignore закрывает registry, journals, outputs и окружение.

Проверки включают recurrence identities/наборы occurrences, COUNT/UNTIL/infinite split, timezone/DST, DATE/floating fixtures, unknown properties/alarms, ETag 412, redirect credential boundaries, независимые encrypted connections, отсутствие secret traceback, atomic claim, restart/idempotency, timeout после принятого create/delete/trim, условную компенсацию, отказ cleanup при чужой правке и проверку фактического read-back evidence.

Тест решений Gate A с mock transport и явно выбранной тестовой меткой environment проверяет только логику; он не является live свидетельством. CI workflow подготовлен, но на GitHub в этом проходе не запускался. Публикации пакетов/remote repository и deployment не выполнялись.

Следующий шаг: локально подключить аккаунт, сделать discovery, выбрать пустой тестовый календарь и запустить матрицу по [инструкции](ICLOUD_PILOT.md). Google/MCP/production backend и реальные семейные события находятся за пределами этого этапа.
