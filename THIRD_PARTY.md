# Прямые зависимости

Лицензии проверены по metadata установленных locked distributions 2026-10-14. Полные тексты лицензий включены в соответствующие upstream packages; собственный MIT не заменяет их условия.

| Distribution | Locked version | License metadata |
|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause |
| icalendar | 7.3.0 | BSD-2-Clause |
| python-dateutil | 2.9.0.post0 | Apache-2.0 / BSD dual license |
| defusedxml | 0.7.1 | PSFL |
| cryptography | 48.0.1 | Apache-2.0 OR BSD-3-Clause |

Transitive и dev dependencies зафиксированы в uv.lock. python-caldav не включён в зависимости текущего пилота. Перед публичной поставкой бинарного bundle отдельно проверить состав и notices всех включённых distributions; сейчас создаются source/wheel собственного Python package без vendoring библиотек.
