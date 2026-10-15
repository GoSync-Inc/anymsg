import pytest


def test_migrations_repeat_without_loss(household):
    household.migrate()
    assert household.one("SELECT label FROM people WHERE id=%s", ("adult-a",))["label"] == "Adult A"


def test_cross_group_binding_fk_rejects_inconsistent_member(household):
    import psycopg

    household.group("other", "Other")
    with pytest.raises(psycopg.IntegrityError):
        household.binding("other", "child", "cal-a")


def test_failed_transaction_does_not_leave_partial_domain_rows(store):
    import psycopg

    with pytest.raises(psycopg.IntegrityError):
        with store.transaction() as conn:
            conn.execute("INSERT INTO people VALUES ('person','Label')")
            conn.execute(
                "INSERT INTO memberships(group_id,person_id,role) VALUES ('missing','person','adult')"
            )
    assert store.one("SELECT * FROM people WHERE id='person'") is None


def test_owner_can_rename_member_without_replacing_identity(household):
    household.person("child", "New name")
    assert household.one("SELECT label FROM people WHERE id='child'")["label"] == "New name"


def test_connection_owner_and_provider_cannot_be_transferred(household):
    from family_calendar.errors import CalendarError

    with pytest.raises(CalendarError, match="connection_identity_changed"):
        household.connection("google-a", "adult-b", "google")
    with pytest.raises(CalendarError, match="connection_identity_changed"):
        household.connection("google-a", "adult-a", "icloud")


def test_database_connect_failure_does_not_expose_dsn_or_driver_traceback(monkeypatch):
    import psycopg
    from family_calendar.service.store import Store
    from family_calendar.errors import CalendarError

    def fail(*args, **kwargs):
        raise psycopg.OperationalError("canary-db-secret")

    monkeypatch.setattr(psycopg, "connect", fail)
    with pytest.raises(CalendarError, match="database_unavailable") as error:
        Store("postgresql://owner:canary-db-secret@127.0.0.1/test").migrate()
    assert "canary-db-secret" not in str(error.value)
    assert error.value.__suppress_context__
