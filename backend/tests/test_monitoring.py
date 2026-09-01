"""
Tests for Phase 18 — Monitoring & Reliability:
- Public health checks (no auth needed)
- Admin monitoring status: DB health, job heartbeats, websocket counts
- API metrics recorded across requests
- Notification metrics recorded on a real (dev-mode) email send
- Job heartbeat recording (via the service function directly, since
  waiting on a real scheduler tick in a test would be slow/flaky)
- Backup create/list/verify, including a rejected corrupt/missing file
  and a path-traversal filename rejected
- Admin-only access
- record_error() never raises even when logging itself fails
"""

from app.services import monitoring as monitoring_svc
from app.services import backup as backup_svc
from app.models.monitoring import JobHeartbeatDB


# ---------- Public health ----------

def test_health_check_no_auth_needed(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_db_check_no_auth_needed(client):
    resp = client.get("/health/db")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert "latency_ms" in resp.json()


# ---------- Admin monitoring status ----------

def test_monitoring_status_admin_only(client, signed_up_admin, auth_headers):
    resp = client.get("/admin/monitoring/status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["database"]["status"] == "ok"
    assert "websocket" in body
    assert "total_connections" in body["websocket"]

    invite_code = signed_up_admin["org_invite_code"]
    resp = client.post(
        "/auth/signup",
        json={
            "username": "monitoring_agent_noperm", "email": "monitoring_agent_noperm@example.com",
            "password": "correct-horse-battery", "role": "agent", "display_name": "Agent", "invite_code": invite_code,
        },
    )
    agent_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    resp = client.get("/admin/monitoring/status", headers=agent_headers)
    assert resp.status_code == 403


def test_monitoring_status_reflects_job_heartbeats(client, auth_headers, db_engine):
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine)
    db = Session()
    try:
        monitoring_svc.record_job_heartbeat(db, "reminder_scheduler", "success", 42)
    finally:
        db.close()

    resp = client.get("/admin/monitoring/status", headers=auth_headers)
    assert resp.status_code == 200
    job = next(j for j in resp.json()["background_jobs"] if j["job_name"] == "reminder_scheduler")
    assert job["last_status"] == "success"
    assert job["last_duration_ms"] == 42
    assert job["is_healthy"] is True
    assert resp.json()["all_jobs_healthy"] is True


def test_job_heartbeat_error_recorded_and_unhealthy_when_stale(db_engine):
    from sqlalchemy.orm import sessionmaker
    from datetime import datetime, timedelta
    Session = sessionmaker(bind=db_engine)
    db = Session()
    try:
        monitoring_svc.record_job_heartbeat(db, "webhook_scheduler", "error", 10, error_message="boom")
        row = db.query(JobHeartbeatDB).filter(JobHeartbeatDB.job_name == "webhook_scheduler").first()
        assert row.error_count == 1
        assert row.last_error_message == "boom"
        assert monitoring_svc.heartbeat_is_healthy(row) is True  # just ran, still within the stale threshold

        row.last_run_at = datetime.utcnow() - timedelta(minutes=monitoring_svc.JOB_STALE_THRESHOLD_MINUTES + 5)
        db.commit()
        assert monitoring_svc.heartbeat_is_healthy(row) is False
    finally:
        db.close()


def test_never_run_job_reports_unhealthy():
    row = JobHeartbeatDB(job_name="x", last_run_at=None, run_count=0, error_count=0)
    assert monitoring_svc.heartbeat_is_healthy(row) is False


# ---------- API & notification metrics ----------

def test_api_metrics_recorded_across_requests(client, auth_headers):
    client.get("/health")
    client.get("/health")
    resp = client.get("/admin/monitoring/api-metrics", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_requests"] > 0
    health_entry = next((e for e in body["slowest_endpoints"] if e["endpoint"] == "GET /health"), None)
    assert health_entry is not None
    assert health_entry["request_count"] >= 2


def test_notification_metrics_endpoint_shape(client, auth_headers):
    resp = client.get("/admin/monitoring/notification-metrics", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


def test_notification_metrics_email_channel_increments(client, auth_headers):
    resp = client.post("/auth/me/change-password", json={"current_password": "correct-horse-battery", "new_password": "monitoring-test-pw-1"}, headers=auth_headers)
    assert resp.status_code == 200  # this sends a real security-alert email via services/email.py
    resp = client.get("/admin/monitoring/notification-metrics", headers=auth_headers)
    assert resp.json().get("email", {}).get("sent", 0) >= 1


# ---------- Backups ----------

def test_create_list_and_verify_backup(client, auth_headers):
    resp = client.post("/admin/monitoring/backups", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    filename = body["filename"]
    assert body["checksum_sha256"]

    resp = client.get("/admin/monitoring/backups", headers=auth_headers)
    assert resp.status_code == 200
    assert any(b["filename"] == filename for b in resp.json())

    resp = client.get(f"/admin/monitoring/backups/{filename}/verify", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert resp.json()["checksum_sha256"] == body["checksum_sha256"]


def test_verify_nonexistent_backup_returns_404(client, auth_headers):
    resp = client.get("/admin/monitoring/backups/does-not-exist.db/verify", headers=auth_headers)
    assert resp.status_code == 404


def test_verify_rejects_path_traversal_filename(client, auth_headers):
    resp = client.get("/admin/monitoring/backups/..%2F..%2Fetc%2Fpasswd/verify", headers=auth_headers)
    assert resp.status_code in (400, 404)  # rejected before ever touching the filesystem either way


def test_verify_backup_detects_corrupted_file(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_svc, "BACKUP_DIR", str(tmp_path))
    corrupt_path = tmp_path / "corrupt.db"
    corrupt_path.write_bytes(b"this is not a sqlite database")

    result = backup_svc.verify_backup("corrupt.db")
    assert result["status"] == "error"
    assert "not a valid sqlite" in result["message"].lower()


def test_backups_require_admin(client, signed_up_admin):
    invite_code = signed_up_admin["org_invite_code"]
    resp = client.post(
        "/auth/signup",
        json={
            "username": "monitoring_dispatcher_noperm", "email": "monitoring_dispatcher_noperm@example.com",
            "password": "correct-horse-battery", "role": "dispatcher", "display_name": "Dispatcher", "invite_code": invite_code,
        },
    )
    dispatcher_headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    resp = client.post("/admin/monitoring/backups", headers=dispatcher_headers)
    assert resp.status_code == 403


# ---------- Error tracking ----------

def test_record_error_never_raises_on_bad_db(db_engine):
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=db_engine)
    db = Session()
    db.close()  # a closed session — record_error must swallow the resulting failure, not propagate it
    try:
        monitoring_svc.record_error(db, None, "GET", "/some/path", ValueError("boom"))
    except Exception as e:  # pragma: no cover - this is exactly what must NOT happen
        assert False, f"record_error raised: {e}"
