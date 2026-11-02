import asyncio
from contextlib import contextmanager

from app import config, main


class _Rows:
    def __init__(self, one=None, many=None):
        self.one = one
        self.many = many or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class _OverviewConnection:
    def execute(self, query, params=None):
        if "AS mapped" in query:
            return _Rows(one={"mapped": 0, "conflicts": 0, "dead_letters": 0})
        if "FROM application GROUP BY state" in query:
            return _Rows(many=[])
        if "FROM job GROUP BY lifecycle" in query:
            return _Rows(many=[])
        if "FROM ticket WHERE state" in query:
            return _Rows(one={"count": 0})
        if "SELECT count(*)::int AS count FROM job" in query:
            return _Rows(one={"count": 0})
        if "FROM agent_configuration" in query and "WHERE agent" not in query:
            return _Rows(many=[])
        if "task_name='public_ats_discovery'" in query and "state='DEAD_LETTER'" not in query:
            return _Rows(one=None)
        if "state='DEAD_LETTER'" in query:
            return _Rows(one={"count": 0})
        if "FROM job WHERE lifecycle IN" in query:
            return _Rows(one={"count": 0})
        if "provider='google_sheets'" in query:
            return _Rows(one={"state": "CONNECTED", "last_success_at": None,
                              "last_error_category": None})
        if "FROM sync_state" in query:
            return _Rows(one={"last_success_at": None, "last_attempt_at": None,
                              "retry_count": 0, "dead_letter_count": 0, "error_category": None})
        if "FROM sheet_mapping" in query:
            return _Rows(one={"mapped": 0, "conflicts": 0, "dead_letters": 0})
        if "task_name='google_sheet_sync'" in query:
            return _Rows(one=None)
        if "agent='sheet_sync'" in query:
            return _Rows(one={"enabled": True, "paused": False, "drain": False})
        raise AssertionError(f"unexpected overview query: {query}")


def test_production_overview_reads_sync_state_dead_letter_count(monkeypatch):
    @contextmanager
    def fake_connection():
        yield _OverviewConnection()

    monkeypatch.setattr(main.db, "connection", fake_connection)
    monkeypatch.setattr(config, "DEMO_MODE", False)

    result = asyncio.run(main.overview(None))

    assert result["mode"] == "PRODUCTION"
    assert result["google_sheets"]["state"] == "CONNECTED"
    assert result["google_sheets"]["dead_letters"] == 0
