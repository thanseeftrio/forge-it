from fastapi import APIRouter, HTTPException, status
from backend.app.core.security import SecurityError
from backend.app.schemas.project import ProjectInspectRequest, ProjectInspectResponse
from backend.app.services.project_service import project_service

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("/inspect", response_model=ProjectInspectResponse)
def inspect_project(request: ProjectInspectRequest) -> ProjectInspectResponse:
    """
    Inspect a selected project workspace (KiCad PCB project or Verilog RTL directory).
    Validates the path to ensure security and prevent unauthorized access.
    """
    try:
        return project_service.inspect_project(request.project_path)
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
            detail=f"Inspection failed: {str(e)}",
        )

