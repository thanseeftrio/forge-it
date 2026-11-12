"""Module 4: Execution Coordinator for the FORGE IT AI Agent Engine.

Responsibilities:
- Send approved tool requests to Member 1's backend (via in-process service callback or HTTP API).
- Receive task IDs (`task_id`) and actual execution results (`BackendExecutionResponse`).
- Poll asynchronous jobs when status is `"queued"` or `"running"`.
- Handle errors, timeouts, and unavailable tools cleanly.
- Never execute arbitrary AI-generated shell commands.
- Strictly enforce user approval (`ApprovalStatus.APPROVED`) before executing any tool that modifies files.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Protocol

import httpx

from .schemas import (
    ApprovalDecisionRequest,
    ApprovalStatus,
    BackendExecutionRequest,
    BackendExecutionResponse,
    BackendJobStatusResponse,
    ExecutionPlan,
    PlanStep,
    PlanValidationStatus,
    StepExecutionStatus,
    _new_id,
    _utc_now_iso,
)
from .tool_registry import ToolRegistry, ToolValidationError, get_default_tool_registry


class SecurityApprovalError(PermissionError):
    """Raised when attempting to execute a file-modifying step without explicit user approval."""


class BackendClientProtocol(Protocol):
    """Protocol implemented by Member 1's backend execution service or an HTTP client adapter."""

    def submit_tool_execution(
        self,
        request: BackendExecutionRequest,
    ) -> BackendExecutionResponse:
        """Submit an approved tool execution request and return initial/completed response."""
        ...

    def get_job_status(
        self,
        task_id: str,
    ) -> BackendJobStatusResponse:
        """Fetch status and result for a previously submitted task_id."""
        ...


class HttpBackendClient:
    """FastAPI-compatible HTTP client for communicating with Member 1's backend endpoints."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout_seconds: float = 15.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def submit_tool_execution(
        self,
        request: BackendExecutionRequest,
    ) -> BackendExecutionResponse:
        """POST an approved tool request to `/api/agent/execute-tool` (or `/api/agent/run`)."""
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self._transport,
            ) as client:
                resp = client.post(
                    "/api/agent/execute-tool",
                    json=request.model_dump(mode="json"),
                )
                resp.raise_for_status()
                return BackendExecutionResponse.model_validate(resp.json())
        except httpx.TimeoutException:
            return BackendExecutionResponse(
                task_id=_new_id("task_timeout"),
                status="timeout",
                tool_name=request.tool_name,
                error=f"Backend execution timed out after {self.timeout_seconds}s for tool '{request.tool_name}'.",
                completed_at=_utc_now_iso(),
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            status_literal = "unavailable" if status_code in (404, 503) else "failed"
            return BackendExecutionResponse(
                task_id=_new_id("task_http_err"),
                status=status_literal,
                tool_name=request.tool_name,
                error=f"Backend HTTP {status_code} error while invoking '{request.tool_name}'.",
                completed_at=_utc_now_iso(),
            )
        except Exception as exc:
            return BackendExecutionResponse(
                task_id=_new_id("task_conn_err"),
                status="unavailable",
                tool_name=request.tool_name,
                error=f"Backend communication error for tool '{request.tool_name}': {type(exc).__name__}",
                completed_at=_utc_now_iso(),
            )

    def get_job_status(
        self,
        task_id: str,
    ) -> BackendJobStatusResponse:
        """Poll `GET /api/agent/jobs/{task_id}` on Member 1's backend."""
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self._transport,
            ) as client:
                resp = client.get(f"/api/agent/jobs/{task_id}")
                resp.raise_for_status()
                return BackendJobStatusResponse.model_validate(resp.json())
        except httpx.TimeoutException:
            return BackendJobStatusResponse(
                task_id=task_id,
                status="timeout",
                result=BackendExecutionResponse(
                    task_id=task_id,
                    status="timeout",
                    tool_name="unknown",
                    error=f"Polling job '{task_id}' timed out.",
                    completed_at=_utc_now_iso(),
                ),
            )
        except Exception as exc:
            return BackendJobStatusResponse(
                task_id=task_id,
                status="failed",
                result=BackendExecutionResponse(
                    task_id=task_id,
                    status="failed",
                    tool_name="unknown",
                    error=f"Failed to poll job '{task_id}': {type(exc).__name__}",
                    completed_at=_utc_now_iso(),
                ),
            )


class InProcessBackendAdapter:
    """Adapter allowing Member 1's FastAPI backend to register a Python handler function directly."""

    def __init__(
        self,
        executor_fn: Callable[[BackendExecutionRequest], BackendExecutionResponse],
        status_fn: Optional[Callable[[str], BackendJobStatusResponse]] = None,
    ) -> None:
        self._executor_fn = executor_fn
        self._status_fn = status_fn
        self._jobs: Dict[str, BackendExecutionResponse] = {}

    def submit_tool_execution(
        self,
        request: BackendExecutionRequest,
    ) -> BackendExecutionResponse:
        try:
            result = self._executor_fn(request)
            self._jobs[result.task_id] = result
            return result
        except TimeoutError as exc:
            res = BackendExecutionResponse(
                task_id=_new_id("task_timeout"),
                status="timeout",
                tool_name=request.tool_name,
                error=f"Execution timed out: {exc}",
                completed_at=_utc_now_iso(),
            )
            self._jobs[res.task_id] = res
            return res
        except Exception as exc:
            res = BackendExecutionResponse(
                task_id=_new_id("task_err"),
                status="failed",
                tool_name=request.tool_name,
                error=f"Execution handler error: {exc}",
                completed_at=_utc_now_iso(),
            )
            self._jobs[res.task_id] = res
            return res

    def get_job_status(self, task_id: str) -> BackendJobStatusResponse:
        if self._status_fn is not None:
            return self._status_fn(task_id)
        stored = self._jobs.get(task_id)
        if stored is None:
            return BackendJobStatusResponse(task_id=task_id, status="failed", result=None)
        return BackendJobStatusResponse(
            task_id=task_id,
            status=stored.status,
            result=stored,
        )


class ExecutionCoordinator:
    """Module 4: Coordinates safe, registry-validated tool execution with the backend."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        backend_client: Optional[BackendClientProtocol] = None,
        max_poll_attempts: int = 10,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self.registry = registry or get_default_tool_registry()
        self.backend_client: BackendClientProtocol = backend_client or HttpBackendClient()
        self.max_poll_attempts = max_poll_attempts
        self.poll_interval_seconds = poll_interval_seconds

    def apply_approval_decision(
        self,
        plan: ExecutionPlan,
        decision: ApprovalDecisionRequest,
    ) -> ExecutionPlan:
        """Record a user's approval or rejection decision on a plan's file-modifying steps."""
        if decision.plan_id != plan.plan_id:
            raise ValueError(
                f"Approval decision plan_id '{decision.plan_id}' does not match plan '{plan.plan_id}'."
            )

        target_ids = set(decision.step_ids) if decision.step_ids else None
        updated_steps: List[PlanStep] = []

        for step in plan.steps:
            if not step.requires_approval:
                updated_steps.append(step)
                continue
            if target_ids is not None and step.step_id not in target_ids:
                updated_steps.append(step)
                continue

            if decision.approved:
                updated_steps.append(
                    step.model_copy(
                        update={
                            "approval_status": ApprovalStatus.APPROVED,
                            "status": StepExecutionStatus.PENDING,
                        }
                    )
                )
            else:
                updated_steps.append(
                    step.model_copy(
                        update={
                            "approval_status": ApprovalStatus.REJECTED,
                            "status": StepExecutionStatus.REJECTED_BY_USER,
                            "error_message": decision.user_note or "User rejected file modification.",
                        }
                    )
                )

        still_requires_approval = any(
            s.requires_approval and s.approval_status == ApprovalStatus.PENDING_APPROVAL
            for s in updated_steps
        )
        new_validation_status = (
            PlanValidationStatus.REQUIRES_APPROVAL
            if still_requires_approval
            else PlanValidationStatus.VALID
        )

        return plan.model_copy(
            update={
                "steps": updated_steps,
                "requires_user_approval": still_requires_approval,
                "validation_status": new_validation_status,
            }
        )

    def execute_plan(
        self,
        plan: ExecutionPlan,
        user_approved_modifications: bool = False,
    ) -> Tuple[ExecutionPlan, List[BackendExecutionResponse]]:
        """Execute all runnable steps in `plan` via Member 1's backend.

        Enforces:
        1. Only tools registered and available in `ToolRegistry` can be called.
        2. All tool arguments are re-validated through `ToolRegistry.validate_call()`.
        3. Any step with `modifies_files=True` or `requires_approval=True` is BLOCKED
           unless `step.approval_status == ApprovalStatus.APPROVED` or `user_approved_modifications=True`.
        """
        if user_approved_modifications and plan.requires_user_approval:
            plan = self.apply_approval_decision(
                plan,
                ApprovalDecisionRequest(plan_id=plan.plan_id, approved=True),
            )

        executed_responses: List[BackendExecutionResponse] = []
        updated_steps: List[PlanStep] = []
        completed_step_numbers: set[int] = set()

        for step in plan.steps:
            # Pure explanation step (no external tool call)
            if step.tool_name is None:
                completed_step_numbers.add(step.step_number)
                updated_steps.append(
                    step.model_copy(update={"status": StepExecutionStatus.COMPLETED})
                )
                continue

            # Check dependencies
            if any(dep not in completed_step_numbers for dep in step.depends_on):
                updated_steps.append(
                    step.model_copy(
                        update={
                            "status": StepExecutionStatus.SKIPPED,
                            "error_message": "Skipped because a prerequisite step did not complete.",
                        }
                    )
                )
                continue

            # Check user rejection
            if step.approval_status == ApprovalStatus.REJECTED:
                updated_steps.append(
                    step.model_copy(update={"status": StepExecutionStatus.REJECTED_BY_USER})
                )
                continue

            # Re-verify tool against ToolRegistry (never trust unvalidated plan state)
            try:
                tool_def = self.registry.require(step.tool_name)
            except ToolValidationError as exc:
                updated_steps.append(
                    step.model_copy(
                        update={
                            "status": StepExecutionStatus.FAILED,
                            "error_message": str(exc),
                        }
                    )
                )
                continue

            # Enforce approval requirement for file modifications
            if tool_def.modifies_files or tool_def.requires_approval or step.requires_approval:
                if step.approval_status != ApprovalStatus.APPROVED:
                    updated_steps.append(
                        step.model_copy(
                            update={
                                "approval_status": ApprovalStatus.PENDING_APPROVAL,
                                "status": StepExecutionStatus.AWAITING_APPROVAL,
                            }
                        )
                    )
                    continue

            # Check tool availability
            if not tool_def.is_available or not step.tool_available:
                err_msg = f"Tool '{tool_def.name}' is currently unavailable on the backend."
                unavail_resp = BackendExecutionResponse(
                    task_id=_new_id("task_unavail"),
                    status="unavailable",
                    tool_name=tool_def.name,
                    error=err_msg,
                    completed_at=_utc_now_iso(),
                )
                executed_responses.append(unavail_resp)
                updated_steps.append(
                    step.model_copy(
                        update={
                            "status": StepExecutionStatus.BLOCKED_UNAVAILABLE_TOOL,
                            "task_id": unavail_resp.task_id,
                            "execution_result": unavail_resp.model_dump(mode="json"),
                            "error_message": err_msg,
                        }
                    )
                )
                continue

            # Validate arguments strictly before calling backend
            try:
                validated_call = self.registry.validate_call(
                    tool_def.name,
                    step.arguments,
                    require_available=True,
                )
            except ToolValidationError as exc:
                updated_steps.append(
                    step.model_copy(
                        update={
                            "status": StepExecutionStatus.FAILED,
                            "error_message": str(exc),
                        }
                    )
                )
                continue

            project_id = (
                validated_call.arguments.get("project_id")
                or plan.project_id
                or "default_project"
            )

            exec_request = BackendExecutionRequest(
                plan_id=plan.plan_id,
                step_id=step.step_id,
                project_id=str(project_id),
                application=tool_def.application,
                tool_name=validated_call.tool_name,
                arguments=validated_call.arguments,
                approved_by_user=(step.approval_status == ApprovalStatus.APPROVED),
            )

            # Dispatch to backend
            response = self._submit_and_resolve(exec_request)
            executed_responses.append(response)

            if response.status == "completed" and (response.exit_code in (None, 0) or response.structured_output):
                # Note: KiCad DRC/ERC or Verilator lint may return exit_code != 0 when violations are found,
                # while still having status="completed" because the tool itself ran and produced a report.
                completed_step_numbers.add(step.step_number)
                step_status = StepExecutionStatus.COMPLETED
                err_text = None
            elif response.status == "unavailable":
                step_status = StepExecutionStatus.BLOCKED_UNAVAILABLE_TOOL
                err_text = response.error or f"Tool '{tool_def.name}' is unavailable."
            else:
                step_status = StepExecutionStatus.FAILED
                err_text = (
                    response.error
                    or response.stderr.strip()
                    or f"Tool '{tool_def.name}' failed with status '{response.status}'."
                )

            updated_steps.append(
                step.model_copy(
                    update={
                        "status": step_status,
                        "task_id": response.task_id,
                        "execution_result": response.model_dump(mode="json"),
                        "error_message": err_text,
                    }
                )
            )

        updated_plan = plan.model_copy(update={"steps": updated_steps})
        return updated_plan, executed_responses

    def _submit_and_resolve(
        self,
        request: BackendExecutionRequest,
    ) -> BackendExecutionResponse:
        """Submit a tool execution request and poll if the backend returns queued/running."""
        initial = self.backend_client.submit_tool_execution(request)
        if initial.status not in ("queued", "running"):
            return initial

        task_id = initial.task_id
        for _ in range(self.max_poll_attempts):
            if self.poll_interval_seconds > 0:
                time.sleep(self.poll_interval_seconds)
            job_status = self.backend_client.get_job_status(task_id)
            if job_status.status not in ("queued", "running"):
                if job_status.result is not None:
                    return job_status.result
                return initial.model_copy(update={"status": job_status.status})

        return BackendExecutionResponse(
            task_id=task_id,
            status="timeout",
            tool_name=request.tool_name,
            error=(
                f"Task '{task_id}' for tool '{request.tool_name}' did not finish "
                f"within {self.max_poll_attempts} poll attempts."
            ),
            completed_at=_utc_now_iso(),
        )
