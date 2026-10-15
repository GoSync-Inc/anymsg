# ADR 001: узкий прямой CalDAV транспорт для пилота

Принято для этапа 1: Python + httpx для CalDAV, icalendar для сохранения/изменения ICS, python-dateutil для recurrence expansion и defusedxml для XML.

План рассматривал python-caldav как первого кандидата. В пилоте выбран небольшой прямой клиент, чтобы явно видеть условные headers, redirects, отсутствие automatic mutation retry и raw server read-back. Это не утверждение об отсутствии таких возможностей у python-caldav: его [документация conditional save/delete](https://caldav.readthedocs.io/latest/caldav/calendarobjectresource.html) описывает `if_match` и поддержку version checks. Если live совместимость потребует другой реализации, её нужно проверить тем же contract/fault набором.

Плюс решения — прозрачный ограниченный поток discovery/GET/REPORT/PUT/DELETE. Стоимость — собственная ответственность за CalDAV interoperability. ACL administration, scheduling, incremental sync, arbitrary server origins и полноценный production connector сейчас не реализованы.

Google интеграция остаётся отдельным прямым Google Calendar API adapter. Общая семейная модель и единый MCP будут поверх adapters. Google Workspace CLI/MCP не является зависимостью этого пилота.

Ключевые протоколы: [CalDAV RFC 4791](https://datatracker.ietf.org/doc/html/rfc4791), [iCalendar RFC 5545](https://datatracker.ietf.org/doc/html/rfc5545), [HTTP conditional requests](https://www.rfc-editor.org/rfc/rfc9110.html#section-13.1).
