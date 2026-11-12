from fastapi import APIRouter
from backend.app.schemas.integration import (
    IntegrationDetectRequest,
    IntegrationDetectResponse,
    IntegrationsListResponse,
)
from backend.app.services.integration_manager import integration_manager

router = APIRouter(prefix="/integrations", tags=["Integrations"])


@router.get("", response_model=IntegrationsListResponse)
def list_integrations() -> IntegrationsListResponse:
    """List all available engineering integrations and their status."""
    items = integration_manager.get_all_integrations()
    return IntegrationsListResponse(integrations=items, count=len(items))


@router.post("/detect", response_model=IntegrationDetectResponse)
def detect_installed_integrations(
    request: IntegrationDetectRequest = IntegrationDetectRequest(),
) -> IntegrationDetectResponse:
    """Detect installed engineering software and adapter modules on host."""
    detected = integration_manager.detect_integrations(division=request.division)
    installed_count = sum(1 for item in detected if item.installed)
    summary = (
        f"Detected {installed_count} of {len(detected)} engineering tool suites installed on host system."
    )
    return IntegrationDetectResponse(detected=detected, summary=summary)

