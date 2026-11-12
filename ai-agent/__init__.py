"""FORGE IT AI Agent Engine.

Provides engineering request understanding, execution planning, tool registry
validation, backend execution coordination, and result analysis for KiCad (PCB)
and Verilator/IC (RTL) workflows.
"""

from .schemas import (
    ApplicationType,
    ApprovalDecisionRequest,
    ApprovalStatus,
    BackendExecutionRequest,
    BackendExecutionResponse,
    BackendJobStatusResponse,
    CorrectedSnippet,
    EngineeringFinding,
    ExecutionPlan,
    IssueSeverity,
    OperationCategory,
    PlanStep,
    PlanValidationStatus,
    ProjectContext,
    RequestUnderstanding,
    ResultAnalysis,
    StepExecutionStatus,
    ToolCallRequest,
    ToolDefinition,
    ToolParameterSchema,
    AgentTurnRequest,
    AgentTurnResponse,
)
from .tool_registry import ToolRegistry, get_default_tool_registry
from .provider import GeminiProvider, LLMProvider, MockGeminiProvider
from .planner import PlanningEngine
from .result_analyzer import ResultAnalyzer
from .coordinator import ExecutionCoordinator, BackendClientProtocol, InProcessBackendAdapter, HttpBackendClient
from .agent import ForgeAgent
from .fastapi_integration import create_agent_router

__all__ = [
    "ApplicationType",
    "ApprovalDecisionRequest",
    "ApprovalStatus",
    "BackendExecutionRequest",
    "BackendExecutionResponse",
    "BackendJobStatusResponse",
    "CorrectedSnippet",
    "EngineeringFinding",
    "ExecutionPlan",
    "IssueSeverity",
    "OperationCategory",
    "PlanStep",
    "PlanValidationStatus",
    "ProjectContext",
    "RequestUnderstanding",
    "ResultAnalysis",
    "StepExecutionStatus",
    "ToolCallRequest",
    "ToolDefinition",
    "ToolParameterSchema",
    "AgentTurnRequest",
    "AgentTurnResponse",
    "ToolRegistry",
    "get_default_tool_registry",
    "GeminiProvider",
    "LLMProvider",
    "MockGeminiProvider",
    "PlanningEngine",
    "ResultAnalyzer",
    "ExecutionCoordinator",
    "BackendClientProtocol",
    "InProcessBackendAdapter",
    "HttpBackendClient",
    "ForgeAgent",
    "create_agent_router",
]
