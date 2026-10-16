# Backup и восстановление Messaging

Операторский механизм, недоступный через MCP/REST. Он не сохраняет личный Core,
другие приложения или ещё не полученную историю провайдеров. Автоматический job,
зашифрованная доставка на вторую VM и retention настроены на синтетическом deployment.
Обе VM относятся к одному provider account; отдельная от него копия recovery key и
независимое облачное хранилище остаются открытыми пунктами до финальной приёмки.

## Состав и согласованность

`snapshot` без `--recovery-config` сохраняет прежний format1: согласованный PG dump,
manifest всех таблиц этой версии и все referenced immutable originals.
С приватным recovery config создаётся format2: дополнительно настройки, холодные
provider sessions и durable spool. Абсолютные исходные пути в manifest не пишутся;
внутри зашифрованного tar файлы состояния имеют имена по SHA256.

Перед полным снимком оператор корректно останавливает **только работающие сборщики**
своего deployment. После снимка возобновляет только их. API/база остаются доступны.
Нельзя копировать SQLite/LevelDB работающего провайдера как обычный каталог.
Код держит те же session flocks, проверяет отсутствие активной DB lease и наличие
session+spool для каждого реального аккаунта, включая временно выключенные.
Lock-файл должен существовать после pairing/первого запуска; backup открывает его
read-only и никогда не создаёт от root. Холодная блокировка снимается после копирования
состояния и PG dump; immutable originals копируются и шифруются после возобновления
сборщиков. `captured_at` показывает время снимка БД, а не окончания доставки.
Полнота списка аккаунтов повторно проверяется внутри экспортируемого PG snapshot.
Блокировка действует и на pairing. Внешний клиент, игнорирующий эти блокировки,
нарушает условие единственного владельца и должен быть остановлен отдельно.

В конфигурации ниже пути относятся к файловой системе **процесса backup**. При
контейнерном запуске оператор явно монтирует нужные каталоги. `session_path` должен
точно соответствовать файлу/каталогу, который collector использует для session lock.
Для каждого второго аккаунта добавляется своя запись и свои источники. Источники
не могут перекрываться или содержать каталог назначения.

```json
{
  "accounts": [
    {"account": "telegram-primary", "session_path": "/state/telegram/telegram.session"}
  ],
  "sources": [
    {"name": "telegram-session", "kind": "session", "account": "telegram-primary", "path": "/state/telegram"},
    {"name": "telegram-spool", "kind": "spool", "account": "telegram-primary", "path": "/spools/telegram"},
    {"name": "settings", "kind": "settings", "account": null, "path": "/settings"}
  ]
}
```

Файл config имеет mode0600. Settings могут быть одним файлом либо каталогом;
следует включить нужные account config, permits, DB/bootstrap settings и закреплённый
код/compose для восстановления. Закрытый age key хранится отдельно от источника.
Пустой список accounts разрешён только при отсутствии реальных аккаунтов в БД;
такой снимок явно не объявляется содержащим provider sessions.

```sh
python -m messaging_service snapshot --snapshot-dir PRIVATE_NEW_DIRECTORY \
  --dump-command '["pg_dump", "-U", "OWNER_ROLE", "-d", "DATABASE"]' \
  --recovery-config PRIVATE_RECOVERY_CONFIG
```

Dump command — доверенный argv оператора, без shell и пароля в аргументах.
Используется [`pg_dump --snapshot`](https://www.postgresql.org/docs/18/app-pgdump.html).
Снимок проверяет изменение файлов во время чтения, SHA256/size, свободный диск,
лимиты 100000 элементов состояния и20GiB. Симлинки, hardlinks, FIFO и прочие special
files отвергаются. При оставшихся Chromium Singleton-ссылках после аварии сначала
проверить остановку профиля; backup сам их не удаляет и не игнорирует ошибку.

`backup_crypto.encrypt_snapshot` проверяет весь bundle и потоково шифрует закреплённым
[age1.3.2](https://github.com/FiloSottile/age/releases/tag/v1.3.2). Только после
успешного завершения появляется конечный ciphertext. На целевое хранилище передаётся
зашифрованный файл; его наличие на том же VPS не считается независимым backup.

Начиная с0.9.4 операторский worker включает `consume_staged_blobs=True`: после
полной проверки snapshot удаляет только собственную временную копию каждого
original сразу после её чтения в tar/age. Ciphertext занимает освобождённое место,
поэтому не нужно одновременно держать две полные копии originals. Исходный volume
смонтирован read-only. При ошибке staging уже может быть частично потреблён:
runner удаляет только каталог этого job и начинает новый capture. Прерванный до
доставки capture (таймаут, SIGTERM, ошибка Docker) удаляется целиком, включая
частичный `.encrypt-*` и неотправленный `bundle.tar.age`; каталоги с `delivery.json`
и пакет, ожидающий повторной доставки, не затрагиваются. Каталоги прерванных job,
оставшиеся от версий до этого исправления, runner сам не удаляет: оператор удаляет
вручную только каталоги без `delivery.json`, чей ID не указан в `journal.json`. Ручной вызов
`encrypt_snapshot` сохраняет snapshot по умолчанию; opt-in принимает только bool
и отвергает hardlinks в staging до начала шифрования.

Свободного места должно хватать на полный snapshot, самый большой original,
metadata и загрузки, продолжающиеся после cold capture. На receiver дополнительно
нужно место для нового ciphertext **до** удаления старых подтверждённых копий.
Перед ростом архива оператор явно увеличивает `--max-bytes`/`--quota-bytes` в
ограниченном forced command, сохраняя резерв диска и запрет shell/read/delete.
Для restore пакета больше20GiB явно задаёт подходящий `decrypt_snapshot(max_bytes=...)`
и проверяет свободное место в карантине. Ключ расшифровки на исходную VM не переносится.

Для локального ASR recovery settings должны включать профиль, его отдельный DSN,
compose overlays и pinned revision/SHA256 модели. Модель можно повторно загрузить
по provenance вместо копирования весов в каждый backup. `pg_dump --no-owner --no-acl`
не сохраняет глобальные PostgreSQL роли: оператор отдельно создаёт ограниченный
ASR login/grants и согласует DSN **до** запуска worker в восстановленной среде.

## Restore drill

1. Дешифровать в **новый** private каталог через `decrypt_snapshot`. Проверяются
   аутентификация age, допустимые tar members, лимит объёма, manifest/dump/blobs/state
   hashes. Нет `extractall`, симлинков, абсолютных путей, `..`, дубликатов или лишних
   неучтённых файлов. Прерванный restore не публикует каталог назначения.
2. Создать пустую отдельную БД с именем, заканчивающимся `_restore_test`.
   Восстановить `pg_restore --exit-on-error --no-owner --no-acl`. Не монтировать
   production DSN и не подключать provider workers/сеть провайдеров.
3. `verify-restore` сравнивает все table counts/hashes и referenced file bytes/hash.
   Проверять **до** миграции или quarantine, которые намеренно меняют данные.
4. `quarantine-restore` отзывает все старые API keys, выключает policies, истекает
   leases, ставит coverageunknown. Principals/consumers остаются. Старый backup не
   должен восстанавливать отозванный доступ.
5. `recovery_state.materialize_recovery(bundle, NEW_QUARANTINE_DIRECTORY)` создаёт
   отдельное дерево `alias/root/...`, файлы0600, каталоги0700. Никогда не пишет в
   исходные пути и не запускает sessions/permits. Сохранённый `enabled=true` внутри
   конфигурации — исторические данные: перед переносом в runtime выключить сбор и
   заново выполнить handover/reconciliation. Условия отправки и outbox при их
   добавлении должны включаться в quarantine отдельно.
6. Для upgrade сравнить все неизменяемые data tables до/после, выполнить migration
   дважды. Сохранять runnable старую версию и проверенный encrypted pre-upgrade
   snapshot. Очистить только свои restore DB, plaintext staging и временные процессы.

Проверки в `tests/test_recovery.py` используют вымышленные sessions. Тест транспорта
со stub age проверяет framing, а не криптографию. Реальные pg_dump/pg_restore,
age encrypt/decrypt и migration drill фиксируются отдельными результатами.

## Автоматический запуск без Mac

Для архива, который уже не помещается в дополнительный plaintext/ciphertext
staging на исходной VM, подготовлен opt-in `stream_v2`: [поточная доставка,
совместимость и порядок проверки](BACKUP_STREAMING.md). Старый режим ниже остаётся
default; переключение требует обновления receiver, host runner, backup image и
проверки восстановления. Форматы сохранённых backup-пакетов не меняются.

Для закрытого объектного хранилища доступен отдельный opt-in
[R2 receiver](BACKUP_R2.md): тот же upload-only SSH и `stream_v2`, ключи R2
только на получателе, независимый restore перед сменой защищённой копии.
Установка сама не переключает действующий маршрут доставки.

`backup_runner.py` — отдельная stdlib-программа оператора на исходной VM. Её
пример конфигурации находится в `deploy/backup-runner.example.json`.
Оператор отдельно настраивает scheduler (например systemd timer) и cleanup;
service/timer в этом релизе не поставляются.
Это инфраструктурное резервное копирование; пользовательские Codex routines оно
не переносит и уведомления не отправляет. Конфигурация0600, каталоги0700; image
в рабочей конфигурации закрепляется digest. Пример с `accounts: []` предназначен
только для стенда без реальных аккаунтов. Перед регистрацией каждого аккаунта
добавить его session, spool, credentials/config/permit в RO mounts/recovery sources.
Проверка покрытия остановит backup при пропущенном реальном аккаунте.

При запуске job сохраняет журнал и точные container IDs уже работающих сборщиков; частоту scheduler выбирает оператор. Job
останавливает только их и возобновляет после cold capture. Выключенные сборщики не
запускаются. Повторный процесс отвергается локальной блокировкой, не вмешиваясь в
работу первого. После сбоя журнал позволяет продолжить существующий контейнер;
ошибка Docker inspect не считается отсутствием контейнера. При настройке scheduler предусмотрите вызов cleanup runner после прерывания,
чтобы завершить принадлежащий заданию контейнер и восстановить состояние сборщиков.

Контейнер target`backup` содержит закреплённые PostgreSQL18.6/Python3.13/age1.3.2.
Он имеет384MiB/0.75CPU, read-only rootfs и источники, writable только staging/tmpfs,
без Docker socket, SSH key и закрытого ключа расшифровки. Единственная дополнительная
capability`DAC_OVERRIDE` нужна для чтения приватных файлов UID сборщика через RO
mounts. PG password передаётся через0600 PGPASSFILE в tmpfs, без пароля в argv.
После успешного шифрования plaintext staging удаляется до отправки.

Доставка использует отдельный SSH key, созданный на исходной VM. Ключ не даёт
shell/SFTP/forwarding/read/delete: целевая VM запускает только
`backup_receiver.py` через [`restrict,from=...,command=...`](https://man.openbsd.org/sshd.8#AUTHORIZED_KEYS_FILE_FORMAT).
Receiver code, SSH home и authorized_keys принадлежат root; закрытый archive directory —
непривилегированному upload user. Host key закреплён через уже проверенный admin
канал. Парольный вход и SSH agent не используются. Receiver проверяет framing,
age header, размер и SHA256, записывает с fsync и не заменяет существующий ID.
Повторная доставка того же ID/hash возвращает прежнюю квитанцию. Он проверяет
целостность ciphertext, но не может проверить расшифровку без recovery key.

При ошибке доставки сохраняется тот же зашифрованный пакет и журнал; повторная
попытка не снимает новый snapshot и не останавливает сборщики повторно. Ошибка
сохраняется в `backups/status.json`; там же последние успешные ID/hash/delivery и
`last_snapshot_at`. Устаревание нужно считать от snapshot, а не от позднего retry.
systemd повторяет неудачный job через5минут. Отдельный operator record
`backup-config/last-restore.json` отражает последнюю фактическую проверку restore.

Default template сохраняет локально одну подтверждённую копию. Это не даёт
upload key права удалять удалённые бэкапы. Для одной проверенной копии на
получателе используйте отдельную root-ротацию после независимого restore,
описанную в [BACKUP_STREAMING.md](BACKUP_STREAMING.md); прежний общий prune timer
нужно отключить или заменить до включения этой политики. Во время проверки
замены временно хранятся обе копии. Старый режим ежедневной ротации сохраняет
последние24, все копии моложе24часов, дневные за7дней, недельные за4недели и защищённую
проверенную точку восстановления. Root-owned`protected.json` содержит её ID/size/hash;
без него или при несовпадении hash **ничего не удаляется**. Upload key не может
менять этот файл или запускать retention. Защищённая точка снимается с защиты только
отдельным операторским решением после проверки замены.

Default receiver ограничен30GiB/4096пакетами,20GiB на пакет, с резервом1GiB. Рабочие значения задаёт оператор. Это полные
копии: размеры и частоту нужно пересмотреть до загрузки большого медиаархива.
Например,48 полных копий за сутки быстро исчерпают квоту; лимит даёт явную ошибку,
но не решает задачу вместимости. Измерение роста, переход к incremental/object backup
при необходимости, независимый provider/account, отдельная key copy, доставка
заметных ошибок через выбранный владельцем канал и регулярный restore drill ещё
входят в M18. Частота30минут не доказывает RPO≤1h на живом объёме; синтетический
restore также не доказывает live RTO/session recovery.

### Schema5: права и журнал доступа

Начиная с Messaging0.5 manifest включает23 таблицы, в том числе `audit_log`,
chat/operation permissions и публичные key UUID. До миграции005 требуется
обновить закреплённый backup image:0.4 отвергает schema5. Образ0.5 сохраняет
совместимость с backup схем2–4, поэтому его можно установить и проверить до
обновления действующей БД. При добавлении конфигурации proxy включить её в явный
список settings sources; это не заменяет backup прежнего приложения и его БД.

Restore сначала сверяет все hashes, затем отзывает сохранённые ключи и отключает
сборщики. Нельзя запустить старый gateway0.4 над восстановленной schema5: он
игнорирует новые ограничения чатов/операций. Версии code/schema/role grants
восстанавливаются согласованно. Подробности политики: [ACCESS.md](ACCESS.md).

## Reviews: schema6

Messaging0.6 добавляет в manifest reviews, review_sources и review_counter:26 таблиц.
Установить backup image0.6 до migration006; он читает schema2–6. Старый image0.5
не принимает schema6. В восстановленной копии проверить сохранение авторов,
request UUID, ревизий/provenance и счётчика, затем стандартный quarantine.
Отметки разбора не превращаются в разрешения на send или в изменения ACK.

## Schema 32: event delivery and private feedback

The manifest includes 63 tables, adding subscription consumption, hashed page
receipts, registered receiver metadata, receiver bindings, the durable hint
outbox and key-owned local feedback, original residency, eviction journals,
durable backup fences and independent file-understanding artifacts/profiles.
Supported schema versions are 2–34. Install
the matching backup runtime before applying schema 32; older pinned runtimes
must reject newer schemas rather than silently omit the delivery state.

Receiver signing bytes remain in separate private operator files, never in the
DB tables or packages. Include those files and the dedicated dispatcher DSN in
the explicit encrypted recovery-state configuration. Verify table hashes before
quarantine. Quarantine then disables receivers, cancels pending/leased hints and
removes page receipts, while preserving consumed positions, historical hints
and feedback. Closed subscriptions, revoked API keys and disabled collectors
still require explicit reviewed reconfiguration before any restored runtime
may resume work. The dispatcher must stay stopped during a restore drill.
