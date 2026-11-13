# API Contract

Backend endpoints and schemas agreed between Member 1, Member 2, Member 3, and Members 4/5.

## Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Backend status check |
| `POST` | `/api/projects/upload` | Upload KiCad zip or Verilog files |
| `GET` | `/api/projects/{project_id}` | Fetch project file tree & details |
| `POST` | `/api/agent/run` | Submit analysis task for execution |
| `GET` | `/api/agent/jobs/{job_id}` | Poll job execution status |
| `GET` | `/api/integrations/status` | Check local EDA tool availability |
| `POST` | `/api/integrations/ic/operations` | Directly trigger an allowlisted IC operation (lint, compile, simulate) |

---

## IC Integration Schemas (Member 5 Contract)

### 1. `GET /api/integrations/status` (or `adapter.get_status()`)

#### Response Schema
```json
{
  "primary_tool": "iverilog",
  "tools": {
    "iverilog": {
      "tool": "iverilog",
      "available": true,
      "version": "13.0 (stable) (v13_0)",
      "executable_path": "C:\\path\\to\\iverilog.exe",
      "runtime_path": "C:\\path\\to\\vvp.exe",
      "capabilities": ["lint", "syntax_check", "compile", "simulate"],
      "error": null
    },
    "verilator": {
      "tool": "verilator",
      "available": false,
      "version": null,
      "executable_path": null,
      "capabilities": [],
      "error": "Verilator executable was not found."
    }
  }
}
```

---

### 2. `GET /api/projects/{project_id}` (or `adapter.inspect_project(path)`)

#### Response Schema
```json
{
  "project_path": "/path/to/project",
  "is_valid": true,
  "error": null,
  "total_files": 2,
  "files": [
    {
      "path": "/path/to/project/counter.v",
      "relative_path": "counter.v",
      "size_bytes": 450,
      "lines": 22,
      "modules": ["counter"],
      "includes": [],
      "is_testbench": false,
      "read_error": null
    }
  ],
  "modules": [
    {
      "name": "counter",
      "file": "counter.v",
      "is_testbench": false,
      "is_top_candidate": true
    }
  ],
  "top_module_candidates": ["counter"],
  "warnings": []
}
```

---

### 3. `POST /api/integrations/ic/operations` (or `adapter.execute_operation(op, path, options)`)

#### Request Schema
```json
{
  "operation": "lint",
  "project_path": "/path/to/project",
  "options": {
    "top_module": "counter",
    "source_files": ["counter.v"],
    "verilog_standard": "2012",
    "timeout": 30
  }
}
```

#### Response Schema (Lint Operation)
```json
{
  "operation": "lint",
  "success": true,
  "return_code": 0,
  "tool": "iverilog",
  "tool_version": "13.0 (stable) (v13_0)",
  "sources_analyzed": ["counter.v"],
  "issues_count": {
    "errors": 0,
    "warnings": 0,
    "info": 0
  },
  "issues": [
    {
      "file": "counter.v",
      "line": 12,
      "column": 5,
      "severity": "warning",
      "message": "Port 'clk' is not connected.",
      "raw": "counter.v:12: warning: Port 'clk' is not connected."
    }
  ],
  "raw_stdout": "",
  "raw_stderr": "",
  "execution_time_ms": 142.5,
  "error": null
}
```

#### Response Schema (Compile Operation)
```json
{
  "operation": "compile",
  "success": true,
  "return_code": 0,
  "tool": "iverilog",
  "tool_version": "13.0 (stable) (v13_0)",
  "artifact_path": "/path/to/project/.forge_build/sim_model.vvp",
  "artifact_size_bytes": 9199,
  "sources_compiled": ["counter.v"],
  "issues_count": {
    "errors": 0,
    "warnings": 0
  },
  "issues": [],
  "raw_stdout": "",
  "raw_stderr": "",
  "execution_time_ms": 110.2,
  "error": null
}
```
