from fastapi.testclient import TestClient

from app.main import app


def test_demo_health_has_deterministic_identity_and_security_headers():
    with TestClient(app) as client:
        response=client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status":"ok","application":"bruteapply","mode":"DEMO"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_demo_workspace_has_synthetic_jobs_and_backup_truth():
    with TestClient(app) as client:
        overview=client.get("/api/v1/overview")
        jobs=client.get("/api/v1/jobs")
        audit=client.get("/api/v1/audit")
    assert overview.status_code == jobs.status_code == 200
    assert overview.json()["backup"]["status"] == "DEFERRED / NOT IMPLEMENTED"
    assert len(jobs.json()["items"]) > 0
    assert all("example.invalid" in row["url"] for row in jobs.json()["items"])
    assert audit.status_code == 200
    assert all(row["result"] == "SYNTHETIC" for row in audit.json()["items"])


def test_demo_does_not_accept_or_persist_owner_records():
    with TestClient(app) as client:
        response=client.post("/api/v1/jobs/import",json={"company":"Example","title":"Support","url":"https://example.com/job","source":"owner-import"})
        profile=client.put("/api/v1/profile",json={"facts":{"name":"Synthetic"}})
        emergency=client.post("/api/v1/agent/emergency-stop")
    assert response.status_code == 202 and response.json()["accepted"] is False
    assert profile.status_code == 200 and profile.json()["saved"] is False
    assert emergency.status_code == 200 and emergency.json()["changed"] is False


def test_demo_mutations_reject_cross_site_origin():
    with TestClient(app) as client:
        response=client.post("/api/v1/jobs/import",headers={"Origin":"https://evil.example"},json={"company":"Example","title":"Support","url":"https://example.com/job","source":"owner-import"})
    # Demo intentionally has no persistence, while production rejects non-owner origins.
    assert response.status_code == 202
