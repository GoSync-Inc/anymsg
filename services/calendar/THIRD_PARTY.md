# Direct dependencies

Licenses were checked against the metadata of the installed locked distributions on 2026-10-14. The full license texts are included in the corresponding upstream packages; our own MIT does not replace their terms.

| Distribution | Locked version | License metadata |
|---|---|---|
| httpx | 0.28.1 | BSD-3-Clause |
| icalendar | 7.3.0 | BSD-2-Clause |
| python-dateutil | 2.9.0.post0 | Apache-2.0 / BSD dual license |
| defusedxml | 0.7.1 | PSFL |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| psycopg / psycopg-binary | 3.3.6 | LGPL-3.0-only |
| psycopg-pool | 3.3.3 | LGPL-3.0-only |
| mcp | 1.30.0 | MIT |
| google-auth | 2.62.0 | Apache-2.0 |
| google-auth-oauthlib | 1.5.0 | Apache-2.0 |
| google-api-python-client | 2.201.0 | Apache-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |
| starlette | 0.52.1 | BSD-3-Clause |
| recurring-ical-events | 3.8.2 | LGPL-3.0-or-later |

Transitive and dev dependencies are pinned in uv.lock. python-caldav is not included in the dependencies of the current pilot. Before a public delivery of a binary bundle, separately check the composition and notices of all included distributions; for now only the source/wheel of our own Python package is built, without vendoring libraries.
