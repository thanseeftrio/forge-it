from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class StatusEnum(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    RUNNING = "running"
    QUEUED = "queued"
    PENDING = "pending"


class DiagnosticLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DiagnosticItem(BaseModel):
    level: DiagnosticLevel = DiagnosticLevel.INFO
    code: Optional[str] = None
    message: str
    file: Optional[str] = None
    line: Optional[int] = None
    details: Optional[Dict[str, Any]] = None


class StandardResponse(BaseModel):
    task_id: str = Field(..., description="Unique task identifier")
    status: StatusEnum = Field(..., description="Execution status")
    application: str = Field(..., description="Target application or module name")
    operation: str = Field(..., description="Executed operation name")
    summary: str = Field(..., description="Human-readable summary of the outcome")
    files: List[str] = Field(default_factory=list, description="Associated or generated file paths")
    diagnostics: List[DiagnosticItem] = Field(default_factory=list, description="Diagnostics, warnings, or errors")
    error: Optional[str] = Field(default=None, description="Error message if operation failed")


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str
    environment: str
    timestamp: str

