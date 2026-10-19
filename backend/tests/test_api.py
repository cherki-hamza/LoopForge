import os
import subprocess
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.db.session import init_db


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    test_db_path = tmp_path / "test.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    init_db()


def test_auth_login_logout():
    client = TestClient(app)

    # Invalid login
    res = client.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword"})
    assert res.status_code == 401

    # Valid login
    res = client.post("/api/auth/login", json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD})
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert "loopforge_access_token" in res.cookies

    token = data["access_token"]

    # Check /me with token in cookie
    res = client.get("/api/auth/me", cookies={"loopforge_access_token": token})
    assert res.status_code == 200
    assert res.json()["username"] == settings.ADMIN_USERNAME

    # Check /me with Bearer header
    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200

    # Logout
    res = client.post("/api/auth/logout")
    assert res.status_code == 200


def test_system_stats():
    client = TestClient(app)
    # Login first
    login_res = client.post("/api/auth/login", json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD})
    token = login_res.json()["access_token"]

    res = client.get("/api/system", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert "disk" in data
    assert "jobs" in data
    assert data["disk"]["free_gb"] > 0


def test_upload_invalid_video(tmp_path):
    client = TestClient(app)
    login_res = client.post("/api/auth/login", json={"username": settings.ADMIN_USERNAME, "password": settings.ADMIN_PASSWORD})
    token = login_res.json()["access_token"]

    # Create dummy text file
    dummy_file = tmp_path / "dummy.txt"
    dummy_file.write_text("not a video")

    with open(dummy_file, "rb") as f:
        res = client.post(
            "/api/jobs",
            files={"file": ("test.mp4", f, "video/mp4")},
            data={"target_seconds": 1800, "mode": "fast"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert res.status_code == 400
    assert "Invalid media format" in res.json()["detail"] or "Invalid video" in res.json()["detail"]
