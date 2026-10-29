from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app import db
from app.auth import Principal
from app.rate_limit import enforce_owner_limit


class Cursor:
    def __init__(self,row): self.row=row
    def fetchone(self): return self.row


class FakeConnection:
    def __init__(self,row): self.row=row; self.parameters=None
    def transaction(self): return self
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self,query,params): self.parameters=params; return Cursor(self.row)


def install_connection(monkeypatch,connection):
    @contextmanager
    def provide(): yield connection
    monkeypatch.setattr(db,"connection",provide)


def test_rate_limit_uses_hash_of_verified_subject_and_commits_counter(monkeypatch):
    connection=FakeConnection({"request_count":2});install_connection(monkeypatch,connection)
    principal=Principal("owner@example.com","access-subject-id")
    enforce_owner_limit(principal,"job-import",20,3600)
    assert connection.parameters[2] != principal.email
    assert len(connection.parameters[2]) == 64
    assert connection.parameters[3:]==("job-import",20)


def test_rate_limit_blocks_when_atomic_insert_returns_no_row(monkeypatch):
    install_connection(monkeypatch,FakeConnection(None))
    with pytest.raises(HTTPException) as result:
        enforce_owner_limit(Principal("owner@example.com","subject"),"job-import",20)
    assert result.value.status_code == 429
