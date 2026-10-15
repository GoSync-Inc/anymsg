# Подключение и пилот iCloud

Пилот создаёт и меняет только вымышленные события в выбранном тестовом календаре. Он не приглашает участников, не меняет sharing и не переносит реальные семейные встречи.

## 1. Подготовить Apple Account

1. Включить двухфакторную защиту Apple Account.
2. Открыть [account.apple.com](https://account.apple.com), раздел **Sign-In and Security → App-Specific Passwords**, создать пароль с названием `Family Calendar Pilot`.
3. Использовать этот пароль при локальном подключении. Основной пароль Apple Account сервису не нужен. Порядок и обязательную 2FA описывает [Apple](https://support.apple.com/en-us/102654).
4. Создать **новый пустой календарь Family Calendar Pilot** именно в iCloud. Удобно через [icloud.com/calendar](https://www.icloud.com/calendar); [инструкция Apple](https://support.apple.com/en-ie/guide/icloud/mmfbbb2cf9/icloud). В Apple «Календаре» выбрать раздел iCloud: календарь «На моём Mac» не доступен серверу CalDAV.

Календарь Google, показанный приложением Apple, подключается впоследствии через Google API. Для этого пилота нужен источник iCloud.

## 2. Ввести credentials локально

Из корня репозитория:

```sh
uv sync --locked
uv run family-calendar connection add-icloud --id pilot-a
uv run family-calendar connection list
uv run family-calendar pilot icloud discover --connection pilot-a
```

Ввести email Apple Account, затем пароль приложения в скрытый запрос терминала. Не отправлять пароль в чат, не добавлять его в аргументы команды или файлы Git. Без интерактивного терминала ввод отключён.

Discovery вернёт `read_verified: true` и массив `calendars` с `ref`, названием и `privilege`. Это подтверждает чтение discovery, а не чтение всех событий или право записи. Найти **Family Calendar Pilot** и скопировать его `ref` вида `cal-…`. Если календарь не найден, проверить источник iCloud и повторить discovery; не выбирать личный календарь вместо него.

Один аккаунт не доказывает доступ к другому. Второй подключается отдельным ID и своим паролем:

```sh
uv run family-calendar connection add-icloud --id pilot-b
uv run family-calendar pilot icloud discover --connection pilot-b
```

Повторный `add-icloud` с существующим ID не перезаписывает credentials. Если пароль отозван, подключить новый отдельный ID и заново выполнить discovery/allowlist. Старые отчёты сохранять; перенос существующего журнала на другой ID запрещён.

## 3. Разрешить запись в тестовый календарь

Во всех командах ниже заменить `CALENDAR_REF` на найденный ref. Пример пути `outputs/icloud-pilot-01` — новый приватный каталог одного запуска.

```sh
uv run family-calendar pilot icloud allow-write --connection pilot-a --calendar-ref CALENDAR_REF --test-calendar
uv run family-calendar pilot icloud run --connection pilot-a --calendar-ref CALENDAR_REF --allow-write --run-dir outputs/icloud-pilot-01
```

`--test-calendar` фиксирует выбор оператором отдельного тестового календаря. Allowlist относится к точной паре connection/calendar; серверные `read_only` запрещают запись. При `unknown` privileges право остаётся непроверенным до реальной записи. Флаг `--allow-write` сам по себе allowlist не создаёт.

Матрица ограничена 16 создаваемыми объектами (обычно 13), с отдельными UID/href. Большинство событий начинается в следующий понедельник в 16:00 Asia/Makassar. Fixtures часового пояса Europe/Berlin начинаются 19 октября 2026 и пересекают DST; это намеренная фиксированная дата проверки. Имя события начинается с `[Family Pilot]`. Одиночное CRUD-событие удаляется в ходе теста; остальные остаются до проверки и очистки.

## 4. Проверить отчёт и Apple клиент

```sh
uv run family-calendar pilot icloud status --run-dir outputs/icloud-pilot-01
uv run python scripts/check_pilot_evidence.py --run-dir outputs/icloud-pilot-01 --allow-unaccepted
```

Первая команда ничего не отправляет в сеть. Проверяющий скрипт сверяет scope, intent hashes, фактические read-back ICS и состояние шагов. `--allow-unaccepted` проверяет целостность до визуальной приёмки. Это локальная проверка свидетельств, а не подпись или независимое подтверждение провайдера.

Дождаться синхронизации Apple «Календаря» или iCloud UI. По `manifest.json` определить начальный понедельник. Проверить:

| Сценарий | Что увидеть |
|---|---|
| Одиночный CRUD | Событие после create/update/delete отсутствует; шаги подтверждены журналом |
| Single/cancel/series (`exceptions`) | Вторая встреча индивидуально перенесена на 18:00, третья отменена; после series shift остальные в 17:00, индивидуальное время сохранено |
| Future COUNT/UNTIL | Прошлые встречи в 16:00, будущие в 17:00; перенесённая пятая в 18:00, третья отменена; всего пять встреч |
| Future infinite | То же поведение на границе; будущее остаётся бессрочной серией |
| Native RANGE (`native-range`) | Начиная со второй встречи новое время 17:00; проверить и более поздние недели |
| All-day | Событие перенесено на следующий день; длительность один день |
| Floating | Локальное время отображается последовательно; интерпретация для поиска слотов всё ещё отключена |
| TZ/DST/properties | Europe/Berlin сохраняет 17:00 по обе стороны DST; alarm 15 минут; EXDATE/RDATE не потеряны |

X-свойства проверяет server read-back, поскольку Apple UI может их не показывать. Server echo `RANGE` без правильного поведения клиента не доказывает native future support. Если native probe не работает, отметить это в результате; split future проверяется независимо.

Сравнение `VTIMEZONE` для `Asia/Makassar` и `Europe/Berlin` использует точную последовательность UTC-переходов в интервале 2020–2035. Это позволяет iCloud заменить историю зоны своим эквивалентным описанием; изменения времени, правил, alarms и свойств событий по-прежнему различаются. Другие зоны, дополнительные свойства зоны и сложные определения сравниваются полностью. Поведение за пределами этого интервала, включая дальнее будущее бессрочных серий, пилотом не подтверждается. Генерация определения использует dateutil для корректного момента весеннего DST-перехода.

Сохранить честный результат core и отдельные результаты future/native, а также название/версию клиента. Пример для трёх успешных проверок:

```sh
uv run family-calendar pilot icloud record-visual-check --run-dir outputs/icloud-pilot-01 --result pass --future-result pass --native-range-result pass --checked-all-cases --client 'Apple Calendar, version from About'
uv run family-calendar pilot icloud reconcile --run-dir outputs/icloud-pilot-01
uv run python scripts/check_pilot_evidence.py --run-dir outputs/icloud-pilot-01
```

`--result` относится к core (single, conditional, single/cancel/series, all-day, properties/TZ); future и native имеют отдельные flags. Вместо `pass` использовать `fail`, если обнаружена ошибка, либо `not-checked` для ещё не проверенного future/native. `--checked-all-cases` подтверждает просмотр всех применимых случаев core. По умолчанию отметка хранится как `owner_reported`.

Если клиент проверил агент через Computer Use, использовать `--source agent_observed --evidence outputs/icloud-pilot-01/observations.json`. Manifest содержит `checks`: для каждого просмотренного сценария — `result` (`pass`/`fail`) и `artifacts` (относительные пути к PNG/JPEG и TXT внутри этого run-dir). Например:

```json
{"checks":{"all_day":{"result":"pass","artifacts":["client-observed/all-day.png","client-observed/all-day.txt"]}}}
```

Для полной приёмки нужны все пять core-сценариев, для future — дополнительно три future-сценария; для проверенного native — `native_range_probe`. Пример выше показывает только формат одного сценария. Каждый сценарий требует screenshot и сохранённый текст UI; файлы должны быть owner-only. При записи сохраняются SHA-256 артефактов. Report и evidence checker отклоняют отсутствующие, изменённые или выходящие за run-dir артефакты. Это локальная проверка целостности наблюдений агента, а не независимая подпись Apple или автоматическое распознавание картинки. Источник `agent_observed` не выдаётся за подтверждение владельца.

`reconcile` может продолжить ещё не выполненные шаги матрицы в ранее разрешённом календаре. Не считать это командой исключительно чтения.

`gate_a=accepted_core` разрешает дальнейшую разработку core; `accepted_core_and_future` дополнительно подтверждает simple weekly split. `not_accepted` требует исправления/повторной проверки. Mock environment всегда `not_accepted`. Второй аккаунт и shared calendar остаются `unverified` до отдельного live теста. Неуспех native probe не отключает подтверждённый split; native остаётся unverified и не используется для обычных переносов.

## 5. Сбой и повтор команды

- Сохранить **тот же** `--run-dir`: UID/href и intent закреплены. Новый каталог означает новый набор событий.
- При timeout сначала `status`, затем `reconcile`. Unknown mutation автоматически не повторяется; read-back либо подтверждает цель, либо оставляет `needs_attention`.
- Отсутствие объекта сразу после timeout не разрешает повтор записи. Повторить reconciliation позже; при продолжающейся неопределённости оставить объект/отчёт для анализа.
- При `version_conflict` чужая правка не перезаписывается. Такой шаг не сбрасывается в `prepared` автоматически.
- `conditional:stale-version` с 412 — намеренный отрицательный тест; остальные conflicts нуждаются в разборе.
- После 401/403 либо отзыва локальной записи следующие новые mutations в этой матрице блокируются.
- Не запускать два live процесса для одного run-dir одновременно. SQLite atomic claim защищает отдельный шаг, но полноценные worker leases и межаккаунтные locks входят в следующие этапы.

Отчёт использует `evidence_as_of`: повтор команды не превращает исторический read-back в свежую проверку внешнего состояния. Время `generated_at` обозначает только сборку отчёта.

## 6. Очистить и отключить запись

После сохранения evidence и визуальной проверки:

```sh
uv run family-calendar pilot icloud cleanup --run-dir outputs/icloud-pilot-01
uv run family-calendar pilot icloud disable-write --connection pilot-a --calendar-ref CALENDAR_REF
```

Cleanup удаляет только verified-owned refs с совпадающими последними ETag и semantic hash. Ручные изменения, неизвестные результаты и отсутствующие без deletion intent объекты не удаляются автоматически. Проверить `remaining` и `conflicts`; при ненулевом результате сохранить журнал для разбора. Для повторной cleanup запись должна ещё быть разрешена. Не удалять весь календарь вместо проверки оставшихся объектов.

После начала cleanup матрицу в том же каталоге запустить нельзя (`run_already_cleaned`). Для нового теста — новый run-dir. Journals/reports можно сохранять как приватное историческое evidence.

Отключение allowlist не отзывает сам Apple пароль. Его можно отдельно отозвать на account.apple.com. Apple также автоматически отзывает пароли приложений при смене основного пароля; [источник](https://support.apple.com/en-us/102654).

## Хранение

По умолчанию `.local/` создаётся в текущем рабочем каталоге: credentials зашифрованы Fernet, локальный ключ хранится отдельно в `.local/master.key`; файлы 0600, папка 0700. В `outputs/` находятся приватные ICS до/после, фактические ответы, href, mappings и SQLite journal. Оба каталога исключены из Git и пакетов.

Локальный ключ рядом с конфигурацией защищает от случайной утечки одного файла; пользователь, имеющий доступ к обоим, сможет расшифровать credentials. Это локальный owner-only pilot store, не системный Keychain или production vault. Ключ не терять: сервис не генерирует новый поверх существующих credentials. Для запуска из другого каталога всегда явно задавать `--home /absolute/private/registry` **до** `connection` / `pilot`; при существующем каталоге нужны закрытые права.
