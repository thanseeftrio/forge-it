"""
IC / RTL Adapter for FORGE IT.
Main integration gateway connecting FORGE IT backend and AI agents with EDA tools.
"""

from typing import Any, Dict, List, Optional

from integrations.ic.detector import ICDetector
from integrations.ic.operations import ICOperations
from integrations.ic.project_inspector import RTLProjectInspector


class ICAdapter:
    """
    Unified adapter for IC / RTL electronic design automation workflows.
    Ensures safe, bounded execution and structured JSON outputs.
    """

    SUPPORTED_OPERATIONS = ["lint", "syntax_check", "compile", "simulate"]

    def __init__(self, custom_tool_paths: Optional[List[str]] = None):
        self.detector = ICDetector(custom_paths=custom_tool_paths)
        self.operations = ICOperations(detector=self.detector)

    def get_status(self) -> Dict[str, Any]:
        """
        Check and report availability and version of local EDA tools.
        Directly satisfies backend GET /api/integrations/status contract.
        """
        return self.detector.detect_all()

    def inspect_project(self, project_path: str) -> Dict[str, Any]:
        """
        Statically inspect an RTL project directory to discover files and top-level modules.
        Satisfies backend GET /api/projects/{project_id} inspection requirements.
        """
        inspector = RTLProjectInspector(project_path)
        return inspector.inspect()

    def get_supported_operations(self) -> List[str]:
        """Return the strict allowlist of operations permitted by this adapter."""
        return list(self.SUPPORTED_OPERATIONS)

    def execute_operation(
        self,
        operation: str,
        project_path: str,
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute an approved engineering operation on the target project.
        Enforces allowlist checks, input sanitization, and timeouts.
        Never executes arbitrary shell strings.
        """
        options = options or {}
        op_normalized = operation.strip().lower()

        # Enforce strict operation allowlist
        if op_normalized not in self.SUPPORTED_OPERATIONS:
            return {
                "operation": operation,
                "success": False,
                "error": f"Operation '{operation}' is not supported. Supported operations: {', '.join(self.SUPPORTED_OPERATIONS)}",
                "issues": [],
                "execution_time_ms": 0
            }

        # Validate project path first
        inspector = RTLProjectInspector(project_path)
        val = inspector.validate_path()
        if not val["valid"]:
            return {
                "operation": operation,
                "success": False,
                "error": val["error"],
                "issues": [],
                "execution_time_ms": 0
            }

        timeout = int(options.get("timeout", 30))
        top_module = options.get("top_module")
        source_files = options.get("source_files")
        verilog_standard = options.get("verilog_standard", "2012")

        if op_normalized in ("lint", "syntax_check"):
            return self.operations.run_lint(
                project_path=project_path,
                source_files=source_files,
                top_module=top_module,
                verilog_standard=verilog_standard,
                timeout=timeout
            )

        elif op_normalized == "compile":
            output_filename = options.get("output_filename", "sim_model.vvp")
            return self.operations.run_compile(
                project_path=project_path,
                source_files=source_files,
                output_filename=output_filename,
                top_module=top_module,
                verilog_standard=verilog_standard,
                timeout=timeout
            )

        elif op_normalized == "simulate":
            vvp_file = options.get("vvp_file")
            return self.operations.run_simulate(
                project_path=project_path,
                vvp_file=vvp_file,
                timeout=timeout
            )

        # Fallback safeguard
        return {
            "operation": operation,
            "success": False,
            "error": f"Unhandled operation: {operation}",
            "execution_time_ms": 0
        }
