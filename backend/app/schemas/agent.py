from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    step_number: int
    application: str = Field(..., description="Target application (e.g., 'KiCad', 'Verilator')")
    operation: str = Field(..., description="Allowed operation identifier")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Command parameters or flags")
    description: str = Field(..., description="Explanation of what this step will do")
    requires_approval: bool = Field(default=True, description="Whether explicit approval is mandatory")
    is_destructive: bool = Field(default=False, description="Whether this step modifies design files")


class PlanRequest(BaseModel):
    project_path: str = Field(..., description="Path to the target hardware/HDL project")
    instruction: str = Field(..., description="User prompt or task, e.g. 'Run DRC and explain issues'")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Optional extra context")


class PlanResponse(BaseModel):
    plan_id: str
    project_path: str
    instruction: str
    summary: str
    steps: List[PlanStep]
    requires_approval: bool = True
    agent_provider: str = Field(default="fallback", description="Name of the reasoning engine used")


class ExecuteRequest(BaseModel):
    plan_id: Optional[str] = Field(default=None, description="Identifier of the generated plan")
    project_path: str = Field(..., description="Path to project")
    operation: str = Field(..., description="Operation to execute")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Execution parameters")
    approved: bool = Field(default=False, description="Explicit approval confirmation from the user")

