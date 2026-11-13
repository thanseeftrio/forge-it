# Integration Guide

This guide details how to integrate the **IC / RTL Integration Module (`integrations.ic`)** with the FORGE IT Backend (Member 1) and the AI Agent (Member 3).

---

## 1. Quick Integration Overview

The `integrations.ic` module provides a single entry point: `ICAdapter`.

```python
from integrations.ic import ICAdapter

adapter = ICAdapter()
```

The adapter is completely stateless, thread-safe, and enforces:
- Strict operation allowlists (`lint`, `syntax_check`, `compile`, `simulate`).
- Safe parameter-list subprocess execution (`shell=False`).
- Execution timeouts (default 30 seconds).
- Path verification to avoid directory traversal.

---

## 2. Integration with Member 1 (FastAPI Backend)

### Route Handler Example (`backend/app/api/routes/integrations.py`)

```python
from fastapi import APIRouter, HTTPException
from integrations.ic import ICAdapter

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
ic_adapter = ICAdapter()

@router.get("/status")
def get_integrations_status():
    """Returns availability of local EDA tools (PCB & IC)."""
    ic_status = ic_adapter.get_status()
    return {
        "status": "ok",
        "ic": ic_status
    }

@router.get("/ic/inspect")
def inspect_ic_project(project_path: str):
    """Discovers RTL files, modules, and top candidates in a project."""
    res = ic_adapter.inspect_project(project_path)
    if not res["is_valid"]:
        raise HTTPException(status_code=400, detail=res["error"])
    return res

@router.post("/ic/execute")
def execute_ic_operation(payload: dict):
    """
    Safely executes an allowlisted operation (lint, compile, simulate).
    Payload shape: {"operation": "lint", "project_path": "...", "options": {}}
    """
    op = payload.get("operation")
    path = payload.get("project_path")
    options = payload.get("options", {})

    result = ic_adapter.execute_operation(op, path, options)
    return result
```

---

## 3. Integration with Member 3 (AI Agent & Tool Registry)

To allow the AI Agent to run verification and linting operations on RTL designs, register `ICAdapter` methods as tools in `ai-agent/tool_registry.py`:

```python
from integrations.ic import ICAdapter

ic_adapter = ICAdapter()

def rtl_lint_tool(project_path: str, top_module: str = None) -> dict:
    """
    Runs RTL syntax check and linting on a Verilog/SystemVerilog project.
    Returns compile errors, warnings, line numbers, and diagnostics.
    """
    return ic_adapter.execute_operation("lint", project_path, {"top_module": top_module})

def rtl_inspect_tool(project_path: str) -> dict:
    """
    Inspects a project folder to find RTL files, declared modules, and top-level candidates.
    """
    return ic_adapter.inspect_project(project_path)

def rtl_compile_tool(project_path: str, top_module: str = None) -> dict:
    """
    Compiles an RTL design into a simulation model using Icarus Verilog.
    """
    return ic_adapter.execute_operation("compile", project_path, {"top_module": top_module})

# Tool specifications for Gemini / Function Calling
TOOLS = [
    {
        "name": "rtl_lint_tool",
        "description": "Lint and verify RTL/Verilog design syntax and signal bindings.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string"},
                "top_module": {"type": "string", "description": "Optional top module name"}
            },
            "required": ["project_path"]
        }
    },
    {
        "name": "rtl_inspect_tool",
        "description": "Scan RTL project directory to discover files and top-level modules.",
        "parameters": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string"}
            },
            "required": ["project_path"]
        }
    }
]
```

### Prompt Guidance for Member 3 (Prompts)
- When the user asks to analyze, check, or fix RTL code, call `rtl_inspect_tool` to identify modules and top candidates.
- Call `rtl_lint_tool` to obtain genuine compiler errors and warnings with line numbers.
- Present line numbers and exact compiler messages to the user.
- **Never guess or invent compilation results.**
