"""Optional disposable-PostgreSQL integration check; never points at production."""
import asyncio
import os

import pytest


@pytest.mark.skipif(not os.getenv("BRUTEAPPLY_TEST_DATABASE_URL"), reason="disposable PostgreSQL URL not configured")
def test_migration_schema_rate_limit_and_encrypted_profile_against_disposable_postgres(monkeypatch):
    import psycopg
    from app import db
    from app.auth import Principal
    from app.rate_limit import enforce_owner_limit

    url=os.environ["BRUTEAPPLY_TEST_DATABASE_URL"]
    with psycopg.connect(url) as conn:
        tables={row[0] for row in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    expected={"candidate_profile","profile_fact","job","application","ticket","document","audit_event","agent_configuration","api_rate_limit"}
    assert expected <= tables
    subject="disposable-test-subject"
    actor=Principal("owner@example.invalid",subject)
    os.environ["DATABASE_URL"]=url
    db.start_pool()
    try:
        enforce_owner_limit(actor,"test-window",2,3600)
        enforce_owner_limit(actor,"test-window",2,3600)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as limited:
            enforce_owner_limit(actor,"test-window",2,3600)
        assert limited.value.status_code==429
        from app import config, main
        monkeypatch.setattr(config,"DEMO_MODE",False)
        monkeypatch.setattr(main,"_document_key",lambda:b"k"*32)
        asyncio.run(main.create_profile(main.ProfileInput(facts={"full_name":"Synthetic Candidate","phone":"555-0100"}),actor))
        before=asyncio.run(main.get_profile(actor))
        assert before["facts"]["full_name"]=="Synthetic Candidate"
        assert before["fact_status"]["full_name"]["verified"] is False
        asyncio.run(main.update_profile(main.ProfileInput(facts={"full_name":"Reviewed Candidate","phone":""}),actor))
        assert asyncio.run(main.verify_profile_fact("full_name",actor))["verified"] is True
        after=asyncio.run(main.get_profile(actor))
        assert after["facts"]=={"full_name":"Reviewed Candidate"}
        assert after["verified_count"]==1 and after["pending_count"]==0
        with pytest.raises(HTTPException) as unimplemented:
            asyncio.run(main.control_agent({"agent":"discovery","action":"START"},actor))
        assert unimplemented.value.status_code==409
        asyncio.run(main.emergency_stop(actor))
        with psycopg.connect(url) as conn:
            stopped=conn.execute("SELECT emergency_stopped FROM agent_configuration WHERE agent='submission'").fetchone()[0]
        assert stopped is True
    finally:
        db.close_pool()
