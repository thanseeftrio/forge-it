from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)


def test_agent_plan_creation_pcb(tmp_path):
    (tmp_path / "board.kicad_pcb").write_text("pcb")
    response = client.post(
        "/api/agent/plan",
        json={
            "project_path": str(tmp_path),
            "instruction": "Check design rules and explain errors",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "plan_id" in data
    assert len(data["steps"]) >= 1
    assert data["requires_approval"] is True

    # At least one step should target KiCad DRC
    operations = [s["operation"] for s in data["steps"]]
    assert "kicad_drc" in operations


def test_agent_plan_creation_ic(tmp_path):
    (tmp_path / "core.v").write_text("module core; endmodule")
    response = client.post(
        "/api/agent/plan",
        json={
            "project_path": str(tmp_path),
            "instruction": "Lint this RTL module",
        },
    )
    assert response.status_code == 200
    data = response.json()
    operations = [s["operation"] for s in data["steps"]]
    assert "verilator_lint" in operations


def test_execute_without_approval_rejected(tmp_path):
    (tmp_path / "board.kicad_pcb").write_text("pcb")
    response = client.post(
        "/api/agent/execute",
        json={
            "project_path": str(tmp_path),
            "operation": "kicad_drc",
            "approved": False,
        },
    )
    assert response.status_code == 400
    assert "requires explicit user approval" in response.json()["detail"]


def test_execute_unauthorized_operation_rejected(tmp_path):
    response = client.post(
        "/api/agent/execute",
        json={
            "project_path": str(tmp_path),
            "operation": "rm_rf_dangerous",
            "approved": True,
        },
    )
    assert response.status_code == 400
    assert "is not permitted" in response.json()["detail"]


def test_execute_safe_inspect_operation(tmp_path):
    (tmp_path / "top.v").write_text("module top; endmodule")
    response = client.post(
        "/api/agent/execute",
        json={
            "project_path": str(tmp_path),
            "operation": "inspect_project",
            "approved": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["operation"] == "inspect_project"
    assert "top.v" in str(data["files"])

