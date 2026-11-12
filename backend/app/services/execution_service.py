import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from backend.app.core.config import settings
from backend.app.core.security import (
    SecurityError,
    sanitize_command_args,
    validate_operation_safety,
    validate_project_path,
)
from backend.app.schemas.common import (
    DiagnosticItem,
    DiagnosticLevel,
    StandardResponse,
    StatusEnum,
)


class ExecutionService:
    """
    Safely executes validated, whitelisted engineering tool operations.
    Enforces shell=False, argument lists, timeouts, and user approval.
    """

    def _delegate_to_adapter(
        self, operation: str, project_path: Path, parameters: Dict[str, Any]
    ) -> Optional[StandardResponse]:
        """Check if Member 4 (PCB) or Member 5 (IC) adapters provide an operations runner."""
        try:
            if operation.startswith("kicad_"):
                from integrations.pcb import operations as pcb_ops  # type: ignore
                if hasattr(pcb_ops, "run_operation"):
                    return pcb_ops.run_operation(operation, str(project_path), parameters)
            elif operation.startswith("verilator_"):
                from integrations.ic import operations as ic_ops  # type: ignore
                if hasattr(ic_ops, "run_operation"):
                    return ic_ops.run_operation(operation, str(project_path), parameters)
        except Exception:
            return None
        return None

    def execute_operation(
        self,
        operation: str,
        project_path_str: str,
        parameters: Optional[Dict[str, Any]] = None,
        approved: bool = False,
        requires_approval: bool = True,
        timeout: Optional[int] = None,
    ) -> StandardResponse:
        """
        Validate and execute an engineering operation safely.
        """
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        parameters = parameters or {}
        effective_timeout = min(timeout or settings.DEFAULT_TIMEOUT_SECONDS, settings.MAX_TIMEOUT_SECONDS)

        # 1. Security & approval verification
        validate_operation_safety(operation, requires_approval=requires_approval, approved=approved)
        target_path = validate_project_path(project_path_str)

        # 2. Check if a team adapter implements this
        adapter_res = self._delegate_to_adapter(operation, target_path, parameters)
        if adapter_res is not None:
            return adapter_res

        # 3. Handle built-in inspection operation
        if operation == "inspect_project":
            from backend.app.services.project_service import project_service
            insp = project_service.inspect_project(str(target_path))
            return StandardResponse(
                task_id=task_id,
                status=StatusEnum.SUCCESS,
                application="ForgeBackend",
                operation=operation,
                summary=insp.summary,
                files=[f.path for f in insp.files],
                diagnostics=[],
                error=None,
            )

        # 4. Map operation to tool executable and argument array
        cmd_args: List[str] = []
        app_name = "EngineeringTool"

        if operation.startswith("kicad_"):
            app_name = "KiCad"
            binary = shutil.which(settings.KICAD_CLI_PATH)
            if not binary:
                err_msg = (
                    f"KiCad CLI ('{settings.KICAD_CLI_PATH}') is not installed or not in system PATH. "
                    f"Integration adapter status: pending/unavailable."
                )
                return StandardResponse(
                    task_id=task_id,
                    status=StatusEnum.ERROR,
                    application=app_name,
                    operation=operation,
                    summary="Execution halted: Tool not installed.",
                    files=[],
                    diagnostics=[DiagnosticItem(level=DiagnosticLevel.ERROR, message=err_msg)],
                    error=err_msg,
                )

            if operation == "kicad_drc":
                # Find the .kicad_pcb file
                pcb_files = list(target_path.glob("*.kicad_pcb")) if target_path.is_dir() else [target_path]
                if not pcb_files or not pcb_files[0].exists():
                    err_msg = "No .kicad_pcb file found in project path for DRC check."
                    return StandardResponse(
                        task_id=task_id,
                        status=StatusEnum.ERROR,
                        application=app_name,
                        operation=operation,
                        summary=err_msg,
                        diagnostics=[DiagnosticItem(level=DiagnosticLevel.ERROR, message=err_msg)],
                        error=err_msg,
                    )
                report_file = target_path / "drc_report.json" if target_path.is_dir() else target_path.parent / "drc_report.json"
                cmd_args = [
                    binary,
                    "pcb",
                    "drc",
                    "--format",
                    "json",
                    "--output",
                    str(report_file),
                    str(pcb_files[0]),
                ]

            elif operation == "kicad_erc":
                sch_files = list(target_path.glob("*.kicad_sch")) if target_path.is_dir() else [target_path]
                if not sch_files or not sch_files[0].exists():
                    err_msg = "No .kicad_sch file found in project path for ERC check."
                    return StandardResponse(
                        task_id=task_id,
                        status=StatusEnum.ERROR,
                        application=app_name,
                        operation=operation,
                        summary=err_msg,
                        diagnostics=[DiagnosticItem(level=DiagnosticLevel.ERROR, message=err_msg)],
                        error=err_msg,
                    )
                cmd_args = [binary, "sch", "erc", str(sch_files[0])]

        elif operation.startswith("verilator_"):
            app_name = "Verilator"
            binary = shutil.which(settings.VERILATOR_PATH)
            if not binary:
                err_msg = (
                    f"Verilator ('{settings.VERILATOR_PATH}') is not installed or not in system PATH. "
                    f"Integration adapter status: pending/unavailable."
                )
                return StandardResponse(
                    task_id=task_id,
                    status=StatusEnum.ERROR,
                    application=app_name,
                    operation=operation,
                    summary="Execution halted: Tool not installed.",
                    files=[],
                    diagnostics=[DiagnosticItem(level=DiagnosticLevel.ERROR, message=err_msg)],
                    error=err_msg,
                )

            if operation == "verilator_lint":
                v_files = list(target_path.glob("*.v")) + list(target_path.glob("*.sv")) if target_path.is_dir() else [target_path]
                if not v_files:
                    err_msg = "No .v or .sv files found for Verilator linting."
                    return StandardResponse(
                        task_id=task_id,
                        status=StatusEnum.ERROR,
                        application=app_name,
                        operation=operation,
                        summary=err_msg,
                        diagnostics=[DiagnosticItem(level=DiagnosticLevel.ERROR, message=err_msg)],
                        error=err_msg,
                    )
                cmd_args = [binary, "--lint-only", "-Wall"] + [str(f) for f in v_files]

        else:
            err_msg = f"No execution runner configured for operation '{operation}'."
            return StandardResponse(
                task_id=task_id,
                status=StatusEnum.ERROR,
                application=app_name,
                operation=operation,
                summary=err_msg,
                error=err_msg,
            )

        # 5. Sanitize arguments and execute via subprocess with shell=False
        try:
            sanitized_args = sanitize_command_args(cmd_args)
            proc = subprocess.run(
                sanitized_args,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                shell=False,
                cwd=str(target_path if target_path.is_dir() else target_path.parent),
            )

            stdout = proc.stdout.strip()
            stderr = proc.stderr.strip()
            diagnostics: List[DiagnosticItem] = []

            if stdout:
                for line in stdout.splitlines()[:50]:  # limit lines for readability
                    diagnostics.append(DiagnosticItem(level=DiagnosticLevel.INFO, message=line))
            if stderr:
                level = DiagnosticLevel.ERROR if proc.returncode != 0 else DiagnosticLevel.WARNING
                for line in stderr.splitlines()[:50]:
                    diagnostics.append(DiagnosticItem(level=level, message=line))

            is_success = proc.returncode == 0
            summary = (
                f"{app_name} '{operation}' completed successfully."
                if is_success
                else f"{app_name} '{operation}' finished with exit code {proc.returncode}."
            )

            return StandardResponse(
                task_id=task_id,
                status=StatusEnum.SUCCESS if is_success else StatusEnum.ERROR,
                application=app_name,
                operation=operation,
                summary=summary,
                files=[],
                diagnostics=diagnostics,
                error=stderr if not is_success else None,
            )

        except subprocess.TimeoutExpired:
            err_msg = f"Execution timed out after {effective_timeout} seconds."
            return StandardResponse(
                task_id=task_id,
                status=StatusEnum.ERROR,
                application=app_name,
                operation=operation,
                summary=err_msg,
                diagnostics=[DiagnosticItem(level=DiagnosticLevel.ERROR, message=err_msg)],
                error=err_msg,
            )
        except Exception as e:
            err_msg = f"Execution error: {str(e)}"
            return StandardResponse(
                task_id=task_id,
                status=StatusEnum.ERROR,
                application=app_name,
                operation=operation,
                summary=err_msg,
                diagnostics=[DiagnosticItem(level=DiagnosticLevel.ERROR, message=err_msg)],
                error=err_msg,
            )


execution_service = ExecutionService()
