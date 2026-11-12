"""FastAPI Router and Integration Helper for Member 1's Backend.

Member 1 can mount this router directly in `backend/app/main.py` or `backend/app/api/routes/agent.py`:

```python
from ai_agent.fastapi_integration import create_agent_router
app.include_router(create_agent_router(), prefix="/api/agent", tags=["AI Agent"])
```
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from .agent import ForgeAgent
from .coordinator import BackendClientProtocol, InProcessBackendAdapter
from .schemas import (
    AgentTurnRequest,
    AgentTurnResponse,
    ApprovalDecisionRequest,
    BackendExecutionRequest,
    BackendExecutionResponse,
    BackendJobStatusResponse,
    ExecutionPlan,
    ProjectContext,
    ResultAnalysis,
)
from pydantic import BaseModel


class ProposeFixRequest(BaseModel):
    """Request payload for generating an approval-gated Fix + Re-verify plan."""

    analysis: ResultAnalysis
    project_context: ProjectContext


def create_agent_router(
    agent: Optional[ForgeAgent] = None,
    tool_executor: Optional[Callable[[BackendExecutionRequest], BackendExecutionResponse]] = None,
):
    """Create a FastAPI `APIRouter` exposing the FORGE IT AI Agent endpoints."""
    from fastapi import APIRouter, HTTPException

    if agent is None:
        backend_client: Optional[BackendClientProtocol] = (
            InProcessBackendAdapter(tool_executor) if tool_executor is not None else None
        )
        agent = ForgeAgent(backend_client=backend_client)

    router = APIRouter()
    jobs_store: Dict[str, BackendExecutionResponse] = {}

    @router.post("/run", response_model=AgentTurnResponse)
    async def run_agent_turn(request: AgentTurnRequest) -> AgentTurnResponse:
        """Understand an engineering request, generate a plan, and execute/analyze if ready."""
        response = agent.handle_turn(request)
        for exec_res in response.execution_results:
            jobs_store[exec_res.task_id] = exec_res
        return response

    @router.post("/approve", response_model=AgentTurnResponse)
    async def approve_plan(decision: ApprovalDecisionRequest) -> AgentTurnResponse:
        """Approve or reject a pending file-modification plan and execute if approved."""
        try:
            response = agent.approve_and_execute(decision)
            for exec_res in response.execution_results:
                jobs_store[exec_res.task_id] = exec_res
            return response
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/plans/{plan_id}", response_model=ExecutionPlan)
    async def get_plan(plan_id: str) -> ExecutionPlan:
        """Fetch a generated execution plan by ID."""
        plan = agent.get_plan(plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found.")
        return plan

    @router.get("/jobs/{job_id}", response_model=BackendJobStatusResponse)
    async def get_job_status(job_id: str) -> BackendJobStatusResponse:
        """Poll execution status for a submitted backend job ID."""
        if job_id in jobs_store:
            res = jobs_store[job_id]
            return BackendJobStatusResponse(
                task_id=job_id,
                status=res.status,
                result=res,
            )
        return agent.coordinator.backend_client.get_job_status(job_id)

    @router.post("/propose-fix", response_model=ExecutionPlan)
    async def propose_fix(request: ProposeFixRequest) -> ExecutionPlan:
        """Generate an approval-gated Fix + Re-verify plan from a ResultAnalysis."""
        plan = agent.propose_fix_plan(request.analysis, request.project_context)
        if plan is None:
            raise HTTPException(
                status_code=400,
                detail="No actionable findings or fix suggestions available to build a fix plan.",
            )
        return plan

    @router.get("/tools")
    async def list_approved_tools():
        """Return all registered and approved KiCad & RTL tools in the Tool Registry."""
        return {
            "tools": [
                t.model_dump(mode="json") for t in agent.registry.list_tools()
            ]
        }

    return router
