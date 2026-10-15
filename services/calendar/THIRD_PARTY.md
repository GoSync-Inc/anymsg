# Прямые зависимости

Лицензии проверены по metadata установленных locked distributions 2026-10-14. Полные тексты лицензий включены в соответствующие upstream packages; собственный MIT не заменяет их условия.

| Distribution | Locked version | License metadata |
|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause |
| icalendar | 7.3.0 | BSD-2-Clause |
| python-dateutil | 2.9.0.post0 | Apache-2.0 / BSD dual license |
| defusedxml | 0.7.1 | PSFL |
| cryptography | 48.0.1 | Apache-2.0 OR BSD-3-Clause |
| psycopg / psycopg-binary | 3.3.6 | LGPL-3.0-only |
| psycopg-pool | 3.3.3 | LGPL-3.0-only |
| mcp | 1.30.0 | MIT |
| google-auth | 2.62.0 | Apache-2.0 |
| google-auth-oauthlib | 1.5.0 | Apache-2.0 |
| google-api-python-client | 2.201.0 | Apache-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |
| starlette | 0.52.1 | BSD-3-Clause |
| recurring-ical-events | 3.8.2 | LGPL-3.0-or-later |

Transitive и dev dependencies зафиксированы в uv.lock. python-caldav не включён в зависимости текущего пилота. Перед публичной поставкой бинарного bundle отдельно проверить состав и notices всех включённых distributions; сейчас создаются source/wheel собственного Python package без vendoring библиотек.
