# Contributing

Use Python 3.13, PostgreSQL 18 and Node 24. All fixtures must be synthetic.
Never commit runtime secrets, sessions, database dumps, recordings or personal data.

```sh
uv sync --group dev
# Point only at an isolated database named messaging_probe_test:
PROBE_TEST_DSN_FILE=/private/test.dsn uv run pytest tests
node --test whatsapp/tests/*.test.mjs
npm test --prefix packages/npm
python scripts/build.py
npm pack --prefix packages/npm --dry-run
```

Python tests deliberately fail if the database fixture is used without an isolated
test DSN. Some OCR tests need `pdftotext` and Tesseract; audio tests explicitly state
when native prerequisites are absent. CI installs those tools. Never use production
credentials in tests.

Keep applied SQL migrations unchanged. Add a new migration for schema changes,
update backup table inventory/restore ceilings and check roles/ACL and repeat migration.
Keep source revisions, original provider IDs and acknowledgement semantics stable.
