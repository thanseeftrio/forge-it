import tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)


def test_inspect_non_existent_project():
    response = client.post("/api/projects/inspect", json={"project_path": "/path/does/not/exist/12345"})
    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]


def test_inspect_forbidden_path():
    response = client.post("/api/projects/inspect", json={"project_path": "/etc"})
    assert response.status_code == 400
    assert "strictly forbidden" in response.json()["detail"]


def test_inspect_pcb_project(tmp_path):
    # Create a simulated KiCad project in a safe temp dir
    pro_file = tmp_path / "board.kicad_pro"
    pcb_file = tmp_path / "board.kicad_pcb"
    pro_file.write_text("kicad pro content")
    pcb_file.write_text("kicad pcb layout")

    response = client.post("/api/projects/inspect", json={"project_path": str(tmp_path)})
    assert response.status_code == 200
    data = response.json()
    assert data["project_type"] == "pcb"
    assert "board.kicad_pcb" in data["main_files"]
    assert "kicad_drc" in data["supported_operations"]


def test_inspect_ic_project(tmp_path):
    # Create a simulated Verilog project in a safe temp dir
    top_v = tmp_path / "alu_top.v"
    sub_v = tmp_path / "adder.sv"
    top_v.write_text("module alu_top(); endmodule")
    sub_v.write_text("module adder(); endmodule")

    response = client.post("/api/projects/inspect", json={"project_path": str(tmp_path)})
    assert response.status_code == 200
    data = response.json()
    assert data["project_type"] == "ic"
    assert "verilator_lint" in data["supported_operations"]

