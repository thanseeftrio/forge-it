from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)


def test_health_check_root():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data
    assert "timestamp" in data


def test_health_check_api_prefix():
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "FORGE IT" in data["message"]
    assert data["docs_url"] == "/docs"

