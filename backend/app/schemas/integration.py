from typing import List, Optional
from pydantic import BaseModel, Field


class IntegrationInfo(BaseModel):
    id: str = Field(..., description="Unique ID of integration, e.g. 'kicad', 'verilator'")
    name: str = Field(..., description="Display name, e.g. 'KiCad EDA'")
    division: str = Field(..., description="Division: 'pcb' or 'ic'")
    description: str = Field(..., description="Short summary of capability")
    binary_name: str = Field(..., description="Primary CLI binary name")
    installed: bool = Field(default=False, description="Whether software is detected on the host")
    version: Optional[str] = Field(default=None, description="Detected version string")
    executable_path: Optional[str] = Field(default=None, description="Resolved binary path on host")
    supported_extensions: List[str] = Field(default_factory=list, description="Supported file extensions")
    supported_operations: List[str] = Field(default_factory=list, description="Permitted operation IDs")
    adapter_available: bool = Field(default=False, description="Whether custom team adapter is present")


class IntegrationsListResponse(BaseModel):
    integrations: List[IntegrationInfo]
    count: int


class IntegrationDetectRequest(BaseModel):
    division: Optional[str] = Field(default=None, description="Filter detection by division ('pcb' or 'ic')")


class IntegrationDetectResponse(BaseModel):
    detected: List[IntegrationInfo]
    summary: str

