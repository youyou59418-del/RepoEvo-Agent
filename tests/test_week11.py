from __future__ import annotations

from fastapi.testclient import TestClient

from apps.api.main import app, container

client = TestClient(app)


def create_task() -> str:
    response = client.post(
        "/api/tasks", json={"task_id": "api-test", "initial_state": {"source": "test"}}
    )
    assert response.status_code == 201
    return response.json()["run_id"]


def test_health_and_task_status() -> None:
    assert client.get("/healthz").json()["ok"] is True
    run_id = create_task()
    response = client.get(f"/api/tasks/{run_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_pause_resume_approve_cancel_and_idempotency() -> None:
    run_id = create_task()
    paused = client.post(f"/api/tasks/{run_id}/pause")
    assert paused.status_code == 200
    assert client.post(f"/api/tasks/{run_id}/pause").json() == paused.json()
    assert client.post(f"/api/tasks/{run_id}/resume").json()["status"] == "queued"
    approved = client.post(f"/api/tasks/{run_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["approved"] is True
    cancelled = client.post(f"/api/tasks/{run_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_sse_events_and_metrics() -> None:
    run_id = create_task()
    response = client.get(f"/api/tasks/{run_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: created" in response.text
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "repoevo_tasks_total" in metrics.text


def test_local_web_origin_is_allowed_by_cors() -> None:
    response = client.options(
        "/api/tasks",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_authorized_artifact_download() -> None:
    run_id = create_task()
    reference = container.artifacts.put_text("api.log", "safe artifact")
    response = client.get(f"/api/tasks/{run_id}/artifacts/{reference.artifact_id}")
    assert response.status_code == 200
    assert response.text == "safe artifact"
