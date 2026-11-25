from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_inspect_invalid_ref_returns_422():
    resp = client.get("/api/datasets/inspect", params={"ref": "invalid-ref"})
    assert resp.status_code == 422


def test_batch_download_invalid_item_creates_failed_task():
    resp = client.post("/api/downloads/batch", json={"items": ["not-a-valid-id"], "format": "csv"})
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) == 1
    assert tasks[0]["status"] == "failed"
    # Clean up task
    del_resp = client.delete(f"/api/downloads/{tasks[0]['task_id']}")
    assert del_resp.status_code == 200


def test_batch_download_invalid_output_dir_returns_422(tmp_path):
    file_path = tmp_path / "not_a_dir.txt"
    file_path.write_text("x", encoding="utf-8")
    resp = client.post(
        "/api/downloads/batch",
        json={"items": ["hwrp-3su3"], "format": "csv", "output_dir": str(file_path)},
    )
    assert resp.status_code == 422
