from fastapi import APIRouter, HTTPException, status
from backend.app.core.security import SecurityError
from backend.app.schemas.agent import ExecuteRequest, PlanRequest, PlanResponse
from backend.app.schemas.common import StandardResponse
from backend.app.services.ai_service import ai_service
from backend.app.services.execution_service import execution_service

router = APIRouter(prefix="/agent", tags=["AI Agent"])


@router.post("/plan", response_model=PlanResponse)
def create_plan(request: PlanRequest) -> PlanResponse:
    """
    Generate an AI execution plan based on the user instruction and project context.
    """
    try:
        return ai_service.create_plan(request)
    except SecurityError as se:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security violation: {str(se)}",
        )
    except FileNotFoundError as fnf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(fnf),
        )
    except FileNotFoundError as fnf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(fnf),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Planning failed: {str(e)}",
        )


@router.post("/execute", response_model=StandardResponse)
def execute_operation(request: ExecuteRequest) -> StandardResponse:
    """
    Execute an approved operation step.
    Enforces user approval verification, security checks, timeouts, and safe execution.
    """
    try:
        return execution_service.execute_operation(
            operation=request.operation,
            project_path_str=request.project_path,
            parameters=request.parameters,
            approved=request.approved,
            requires_approval=True,
        )
    except SecurityError as se:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Security violation: {str(se)}",
        )
    except FileNotFoundError as fnf:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(fnf),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Execution error: {str(e)}",
        )
