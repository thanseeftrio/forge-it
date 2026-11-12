from typing import List, Optional
from pydantic import BaseModel, Field


class ProjectInspectRequest(BaseModel):
    project_path: str = Field(..., description="Path to project directory or file")


class ProjectFileInfo(BaseModel):
    path: str
    name: str
    size_bytes: int
    file_type: str
    is_directory: bool = False


class ProjectInspectResponse(BaseModel):
    project_path: str
    project_name: str
    project_type: str = Field(..., description="'pcb', 'ic', or 'unknown'")
    main_files: List[str] = Field(default_factory=list, description="Key design files (.kicad_pro, top-level .v/.sv)")
    files: List[ProjectFileInfo] = Field(default_factory=list, description="Discovered project files")
    summary: str
    supported_operations: List[str] = Field(default_factory=list, description="Operations supported for this project")

