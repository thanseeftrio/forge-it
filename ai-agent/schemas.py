"""Pydantic schemas and API contract models for the FORGE IT AI Agent Engine.

Defines strongly typed models for:
1. Request Understanding (application, project, intent, requested operation, clarification)
2. Tool Registry definitions and argument validation
3. Planning Engine (step-by-step plans, tool availability, approval flags)
4. Execution Coordinator (backend job submission, polling, approval enforcement)
5. Result Analyzer (verified facts vs. AI suggestions, affected files/lines, corrections)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class ApplicationType(str, Enum):
    """Supported target engineering applications."""

    KICAD = "kicad"
    RTL = "rtl"
    UNKNOWN = "unknown"


class OperationCategory(str, Enum):
    """High-level engineering operation categories."""

    INSPECT_PROJECT = "inspect_project"
    DRC_CHECK = "drc_check"
    ERC_CHECK = "erc_check"
    EXPORT_BOM = "export_bom"
    EXPORT_NETLIST = "export_netlist"
    EXPORT_GERBER = "export_gerber"
    MODIFY_PCB_PARAMETER = "modify_pcb_parameter"
    MODIFY_SCHEMATIC_COMPONENT = "modify_schematic_component"
    RTL_LINT = "rtl_lint"
    RTL_SIMULATE = "rtl_simulate"
    RTL_SYNTHESIS_CHECK = "rtl_synthesis_check"
    GENERATE_RTL_MODULE = "generate_rtl_module"
    MODIFY_RTL_SOURCE = "modify_rtl_source"
    EXPLAIN_CONCEPT = "explain_concept"
    UNSUPPORTED = "unsupported"


class ApprovalStatus(str, Enum):
    """User approval status for operations that modify files."""

    NOT_REQUIRED = "not_required"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"


class StepExecutionStatus(str, Enum):
    """Execution lifecycle status of a single plan step."""

    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED_UNAVAILABLE_TOOL = "blocked_unavailable_tool"
    REJECTED_BY_USER = "rejected_by_user"


class PlanValidationStatus(str, Enum):
    """Overall readiness status of a generated execution plan."""

    VALID = "valid"
    NEEDS_CLARIFICATION = "needs_clarification"
    REQUIRES_APPROVAL = "requires_approval"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    TOOL_UNAVAILABLE = "tool_unavailable"


class IssueSeverity(str, Enum):
    """Severity level of an engineering finding from DRC/ERC/Lint/Simulation."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ProjectContext(BaseModel):
    """Untrusted metadata about the user's active project."""

    model_config = ConfigDict(extra="ignore")

    project_id: Optional[str] = Field(
        default=None,
        description="Unique project identifier registered in the backend.",
    )
    project_name: Optional[str] = Field(
        default=None,
        description="Human-readable name of the project.",
    )
    application: Optional[ApplicationType] = Field(
        default=None,
        description="Detected or user-selected application type (kicad or rtl).",
    )
    files: List[str] = Field(
        default_factory=list,
        description="Relative file paths inside the project workspace.",
    )
    available_tools: Optional[List[str]] = Field(
        default=None,
        description="Optional list of tool names currently available on the backend host.",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional project metadata (e.g. top_module, board_file, schematic_file).",
    )


class RequestUnderstanding(BaseModel):
    """Module 1 output: Structured interpretation of a natural-language engineering request."""

    request_id: str = Field(
        default_factory=lambda: _new_id("req"),
        description="Unique identifier for this request understanding record.",
    )
    raw_query: str = Field(
        description="The original natural-language query from the user.",
    )
    application: ApplicationType = Field(
        description="Target engineering domain/application (kicad, rtl, or unknown).",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="Target project ID if specified or inferred from context.",
    )
    target_files: List[str] = Field(
        default_factory=list,
        description="Specific project files referenced by the request (e.g., 'board.kicad_pcb', 'alu.v').",
    )
    intent_summary: str = Field(
        description="Concise 1-2 sentence summary of what the user wants to accomplish.",
    )
    requested_operations: List[OperationCategory] = Field(
        default_factory=list,
        description="Ordered list of engineering operations requested by the user.",
    )
    extracted_parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted parameters such as top_module, severity_filter, output_format, net_name, etc.",
    )
    modifies_files: bool = Field(
        default=False,
        description="True if any requested operation will modify or overwrite engineering project files.",
    )
    needs_clarification: bool = Field(
        default=False,
        description="True if the request is ambiguous or missing essential information (e.g. missing project/file).",
    )
    clarification_questions: List[str] = Field(
        default_factory=list,
        description="Specific questions to ask the user when needs_clarification is True.",
    )
    unsupported_reason: Optional[str] = Field(
        default=None,
        description="If the user asks for something outside approved tools (e.g. full interactive PCB routing), explain why.",
    )
    confidence: float = Field(
        default=0.9,
        ge=0.0,
        le=1.0,
        description="Confidence score in [0.0, 1.0] for the interpreted intent.",
    )


class ToolParameterSchema(BaseModel):
    """Schema definition for a single tool parameter."""

    name: str = Field(description="Parameter name.")
    type: Literal["string", "integer", "number", "boolean", "array", "object"] = Field(
        default="string",
        description="JSON schema primitive type.",
    )
    description: str = Field(description="Explanation of the parameter.")
    required: bool = Field(default=True, description="Whether this parameter is mandatory.")
    default: Optional[Any] = Field(default=None, description="Default value if omitted.")
    allowed_values: Optional[List[Any]] = Field(
        default=None,
        description="Optional enum whitelist of valid values.",
    )


class ToolDefinition(BaseModel):
    """Metadata and schema for an approved engineering tool in the Tool Registry."""

    name: str = Field(description="Unique canonical tool identifier (e.g. 'kicad_run_drc').")
    application: ApplicationType = Field(description="Engineering domain ('kicad' or 'rtl').")
    category: OperationCategory = Field(description="Operation category this tool fulfills.")
    description: str = Field(description="Clear description of what the tool does and its outputs.")
    parameters: List[ToolParameterSchema] = Field(
        default_factory=list,
        description="Approved parameter definitions.",
    )
    modifies_files: bool = Field(
        default=False,
        description="Whether this tool modifies existing engineering design files.",
    )
    requires_approval: bool = Field(
        default=False,
        description="Whether explicit user approval is required before executing this tool.",
    )
    is_available: bool = Field(
        default=True,
        description="Whether the underlying adapter/CLI is currently available.",
    )

    def to_gemini_function_declaration(self) -> Dict[str, Any]:
        """Convert this tool definition into a Gemini Interactions API function tool dict."""
        properties: Dict[str, Any] = {}
        required_params: List[str] = []
        for param in self.parameters:
            prop: Dict[str, Any] = {
                "type": param.type,
                "description": param.description,
            }
            if param.allowed_values:
                prop["enum"] = param.allowed_values
            properties[param.name] = prop
            if param.required:
                required_params.append(param.name)

        return {
            "type": "function",
            "name": self.name,
            "description": (
                f"[{self.application.value.upper()}] {self.description} "
                f"(modifies_files={self.modifies_files}, requires_approval={self.requires_approval})"
            ),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required_params,
            },
        }


class ToolCallRequest(BaseModel):
    """A validated request to invoke a specific registered tool."""

    tool_name: str = Field(description="Must match an approved tool in ToolRegistry.")
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Validated arguments matching the tool's parameter schema.",
    )


class PlanStep(BaseModel):
    """A single step in an ExecutionPlan."""

    step_id: str = Field(
        default_factory=lambda: _new_id("step"),
        description="Unique identifier for the step.",
    )
    step_number: int = Field(ge=1, description="1-based sequential order of the step.")
    title: str = Field(description="Short human-readable title of the step.")
    description: str = Field(description="Detailed explanation of what this step does and why.")
    operation: OperationCategory = Field(description="Engineering operation category.")
    tool_name: Optional[str] = Field(
        default=None,
        description="Approved tool name from ToolRegistry (None only for pure explanation steps).",
    )
    arguments: Dict[str, Any] = Field(
        default_factory=dict,
        description="Validated arguments to pass to the tool.",
    )
    tool_available: bool = Field(
        default=True,
        description="Whether the required tool is registered and available.",
    )
    modifies_files: bool = Field(
        default=False,
        description="True if this step modifies project files.",
    )
    requires_approval: bool = Field(
        default=False,
        description="True if user approval is required before executing this step.",
    )
    approval_status: ApprovalStatus = Field(
        default=ApprovalStatus.NOT_REQUIRED,
        description="Current user approval state for this step.",
    )
    status: StepExecutionStatus = Field(
        default=StepExecutionStatus.PENDING,
        description="Current execution status of this step.",
    )
    depends_on: List[int] = Field(
        default_factory=list,
        description="List of prior step_numbers that must succeed before this step runs.",
    )
    task_id: Optional[str] = Field(
        default=None,
        description="Backend job/task ID assigned when executed.",
    )
    execution_result: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Raw result returned by the backend for this step.",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message if step validation or execution failed.",
    )


class ExecutionPlan(BaseModel):
    """Module 2 output: Step-by-step execution plan validated against the Tool Registry."""

    plan_id: str = Field(
        default_factory=lambda: _new_id("plan"),
        description="Unique identifier for this execution plan.",
    )
    request_id: str = Field(description="ID of the RequestUnderstanding that produced this plan.")
    application: ApplicationType = Field(description="Target engineering application.")
    project_id: Optional[str] = Field(default=None, description="Target project ID.")
    summary: str = Field(description="Overview of the execution plan.")
    validation_status: PlanValidationStatus = Field(
        description="Whether the plan is valid, needs clarification, requires approval, or is unsupported."
    )
    steps: List[PlanStep] = Field(
        default_factory=list,
        description="Ordered list of execution steps.",
    )
    requires_user_approval: bool = Field(
        default=False,
        description="True if any step in the plan modifies files and awaits approval.",
    )
    missing_tools: List[str] = Field(
        default_factory=list,
        description="List of required tools or operations that are unavailable or unsupported.",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Safety or availability warnings for the user.",
    )
    created_at: str = Field(
        default_factory=_utc_now_iso,
        description="ISO-8601 UTC timestamp when the plan was generated.",
    )


class BackendExecutionRequest(BaseModel):
    """Payload sent by ExecutionCoordinator to Member 1's backend."""

    plan_id: str = Field(description="ID of the parent execution plan.")
    step_id: str = Field(description="ID of the specific plan step.")
    project_id: str = Field(description="Target project ID.")
    application: ApplicationType = Field(description="Target application ('kicad' or 'rtl').")
    tool_name: str = Field(description="Approved tool name.")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Validated tool arguments.")
    approved_by_user: bool = Field(
        default=False,
        description="Explicit confirmation flag when the tool modifies files.",
    )


class BackendExecutionResponse(BaseModel):
    """Response returned by Member 1's backend when a tool execution is requested."""

    task_id: str = Field(description="Unique backend job/task identifier.")
    status: Literal["queued", "running", "completed", "failed", "timeout", "unavailable"] = Field(
        description="Execution status of the backend task."
    )
    tool_name: str = Field(description="Tool that was invoked.")
    exit_code: Optional[int] = Field(
        default=None,
        description="Process exit code from the underlying CLI adapter (if applicable).",
    )
    stdout: str = Field(default="", description="Standard output captured from the tool.")
    stderr: str = Field(default="", description="Standard error captured from the tool.")
    structured_output: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured JSON output parsed by the integration adapter (e.g. DRC violations list).",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if task failed, timed out, or tool was unavailable.",
    )
    completed_at: Optional[str] = Field(
        default=None,
        description="ISO-8601 UTC completion timestamp.",
    )


class BackendJobStatusResponse(BaseModel):
    """Response from `GET /api/agent/jobs/{job_id}`."""

    task_id: str
    status: Literal["queued", "running", "completed", "failed", "timeout", "unavailable"]
    result: Optional[BackendExecutionResponse] = None


class EngineeringFinding(BaseModel):
    """A single verified error, warning, or violation extracted from actual tool output."""

    severity: IssueSeverity = Field(description="Severity ('error', 'warning', or 'info').")
    code: Optional[str] = Field(
        default=None,
        description="Rule or violation code (e.g. 'clearance', 'unconnected_items', 'WIDTH', 'UNOPTFLAT').",
    )
    message: str = Field(description="Exact or normalized message from the tool output.")
    file_path: Optional[str] = Field(
        default=None,
        description="Affected project file path if reported by the tool.",
    )
    line_number: Optional[int] = Field(
        default=None,
        description="Affected line number (1-based) if reported by the tool.",
    )
    column_number: Optional[int] = Field(
        default=None,
        description="Affected column number if reported by the tool.",
    )
    components_or_nets: List[str] = Field(
        default_factory=list,
        description="Affected PCB components/pins/nets or RTL signals/modules (e.g., ['R1', 'GND', 'clk']).",
    )
    verified_from_tool: bool = Field(
        default=True,
        description="Always True for findings extracted directly from actual tool execution output.",
    )


class CorrectedSnippet(BaseModel):
    """An AI-suggested fix or correction for a specific engineering finding."""

    target_file: Optional[str] = Field(
        default=None,
        description="File to which this suggestion applies.",
    )
    line_range: Optional[str] = Field(
        default=None,
        description="Approximate line range or PCB location (e.g., 'L15-L22' or 'Net GND near U1 pin 4').",
    )
    explanation: str = Field(description="Clear engineering explanation of how to fix the issue.")
    suggested_change: Optional[str] = Field(
        default=None,
        description="Optional code snippet or KiCad parameter adjustment.",
    )
    is_ai_suggestion: Literal[True] = Field(
        default=True,
        description="Explicit marker that this correction is an AI suggestion, not a verified tool fact.",
    )


class ResultAnalysis(BaseModel):
    """Module 5 output: Structured analysis of actual tool execution results.

    Strictly separates `verified_facts` (derived directly from actual tool output)
    from `ai_suggestions` (diagnostic advice and candidate corrections).
    """

    analysis_id: str = Field(
        default_factory=lambda: _new_id("ana"),
        description="Unique identifier for this analysis.",
    )
    plan_id: str = Field(description="Associated execution plan ID.")
    task_ids: List[str] = Field(
        default_factory=list,
        description="Backend task IDs whose outputs were analyzed.",
    )
    tool_executed: bool = Field(
        description="True ONLY if the backend actually ran the tool and returned real output."
    )
    execution_succeeded: bool = Field(
        description="True ONLY if the tool ran and completed without fatal execution/system errors."
    )
    status_summary: str = Field(
        description="Concise headline summary of the outcome (never fabricates success)."
    )
    error_count: int = Field(default=0, ge=0, description="Total verified error count.")
    warning_count: int = Field(default=0, ge=0, description="Total verified warning count.")
    verified_facts: List[str] = Field(
        default_factory=list,
        description="Facts strictly verified from the actual tool output (exit code, counts, exact violations).",
    )
    findings: List[EngineeringFinding] = Field(
        default_factory=list,
        description="Structured list of errors/warnings with affected files, lines, and components.",
    )
    plain_english_explanation: str = Field(
        description="Accessible explanation of what the errors and warnings mean for the hardware design."
    )
    ai_suggestions: List[CorrectedSnippet] = Field(
        default_factory=list,
        description="Clearly labeled AI-generated suggestions and possible corrections.",
    )
    next_steps: List[str] = Field(
        default_factory=list,
        description="Recommended follow-up actions for the engineer.",
    )


class ApprovalDecisionRequest(BaseModel):
    """User decision to approve or reject a pending plan or step that modifies files."""

    plan_id: str = Field(description="ID of the plan awaiting approval.")
    step_ids: Optional[List[str]] = Field(
        default=None,
        description="Specific step IDs to approve/reject (None applies to all pending steps in the plan).",
    )
    approved: bool = Field(description="True to approve file modifications, False to reject.")
    user_note: Optional[str] = Field(default=None, description="Optional note from the user.")


class AgentTurnRequest(BaseModel):
    """Top-level input payload for `POST /api/agent/run` or `ForgeAgent.handle_turn()`."""

    query: str = Field(
        min_length=1,
        max_length=8000,
        description="Natural-language engineering request from the user.",
    )
    project_context: ProjectContext = Field(
        default_factory=ProjectContext,
        description="Project context (project_id, files, application, available_tools).",
    )
    auto_execute_read_only: bool = Field(
        default=True,
        description="Whether to automatically execute read-only steps (DRC, ERC, lint, inspect) via the backend.",
    )
    user_approved_modifications: bool = Field(
        default=False,
        description="True ONLY if the user has explicitly approved file-modifying steps.",
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description="Optional conversation or interaction ID for multi-turn continuity.",
    )

    @field_validator("query")
    @classmethod
    def _strip_and_validate_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Query must not be empty or whitespace only.")
        return cleaned


class AgentTurnResponse(BaseModel):
    """Unified response returned to Backend (Member 1) and Frontend (Member 2)."""

    turn_id: str = Field(
        default_factory=lambda: _new_id("turn"),
        description="Unique ID for this agent turn.",
    )
    status: Literal[
        "completed",
        "needs_clarification",
        "awaiting_approval",
        "unsupported",
        "tool_unavailable",
        "execution_failed",
        "plan_ready",
    ] = Field(description="High-level turn status for the frontend UI.")
    understanding: RequestUnderstanding = Field(
        description="Structured request understanding output (Module 1)."
    )
    plan: Optional[ExecutionPlan] = Field(
        default=None,
        description="Structured execution plan (Module 2).",
    )
    execution_results: List[BackendExecutionResponse] = Field(
        default_factory=list,
        description="Actual execution responses from the backend (Module 4).",
    )
    analysis: Optional[ResultAnalysis] = Field(
        default=None,
        description="Structured analysis of actual tool outputs (Module 5).",
    )
    message: str = Field(
        description="Primary user-facing message combining verified facts, explanations, or clarification prompts."
    )
    created_at: str = Field(
        default_factory=_utc_now_iso,
        description="ISO-8601 UTC timestamp.",
    )
