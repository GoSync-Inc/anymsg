import json
import os
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def store():
    from family_calendar.service.store import Store

    config = Path(".local/service/runtime.json")
    dsn = os.environ.get("FCS_TEST_DSN") or (
        json.loads(config.read_text())["dsn"] if config.exists() else None
    )
    if not dsn:
        pytest.skip("isolated PostgreSQL required: FCS_TEST_DSN")
    s = Store(dsn, schema="test_" + uuid4().hex)
    s.migrate()
    yield s
    try:
        s.drop_test_schema()
    finally:
        s.close()


@pytest.fixture
def household(store):
    store.person("adult-a", "Adult A")
    store.person("adult-b", "Adult B")
    store.person("child", "Child")
    store.group("family", "Family")
    store.member("family", "adult-a", "group_admin")
    store.member("family", "adult-b", "adult")
    store.member("family", "child", "dependent")
    store.connection("google-a", "adult-a", "google")
    store.calendar("cal-a", "google-a", "private-a", "Private", "Asia/Makassar", "write")
    return store
