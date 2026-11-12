from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)


def test_list_integrations():
    response = client.get("/api/integrations")
    assert response.status_code == 200
    data = response.json()
    assert "integrations" in data
    assert data["count"] >= 2

    # Check presence of KiCad and Verilator definitions
    ids = [item["id"] for item in data["integrations"]]
    assert "kicad" in ids
    assert "verilator" in ids

    # Check that installed field is a boolean and doesn't invent fake installations
    for item in data["integrations"]:
        assert isinstance(item["installed"], bool)
        assert item["division"] in ["pcb", "ic"]


def test_detect_integrations_all():
    response = client.post("/api/integrations/detect", json={})
    assert response.status_code == 200
    data = response.json()
    assert "detected" in data
    assert "summary" in data
    assert len(data["detected"]) >= 2


def test_detect_integrations_filtered_division():
    response = client.post("/api/integrations/detect", json={"division": "pcb"})
    assert response.status_code == 200
    data = response.json()
    assert all(item["division"] == "pcb" for item in data["detected"])

    response_ic = client.post("/api/integrations/detect", json={"division": "ic"})
    assert response_ic.status_code == 200
    data_ic = response_ic.json()
    assert all(item["division"] == "ic" for item in data_ic["detected"])

