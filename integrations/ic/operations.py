"""
IC Operations module.
Executes approved engineering operations (linting, compilation, simulation) via Icarus Verilog.
Parses compiler and simulator output into structured diagnostics.
"""

import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Set

from integrations.ic.detector import ICDetector
from integrations.ic.project_inspector import RTLProjectInspector


class ICOperations:
    """Executes validated, allowlisted EDA operations with zero shell interpolation."""

    ALLOWED_OPERATIONS: Set[str] = {"lint", "syntax_check", "compile", "simulate"}

    # Pattern for Icarus Verilog error/warning diagnostics:
    # Example 1: counter.v:6: syntax error
    # Example 2: counter.v:10: warning: Port 'clk' is not connected.
    # Example 3: C:\path\file.v:15: error: Undefined module type: foo
    DIAGNOSTIC_PATTERN = re.compile(
        r"^(?P<file>[a-zA-Z]:[\\/][^:\r\n]+|[^:\r\n]+):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?:(?P<severity>warning|error|fatal|syntax error|note):\s*)?(?P<message>.+)$",
        re.MULTILINE | re.IGNORECASE
    )

    def __init__(self, detector: Optional[ICDetector] = None):
        self.detector = detector or ICDetector()

    def parse_diagnostics(self, raw_stderr: str, raw_stdout: str) -> List[Dict[str, Any]]:
        """Parse raw compiler/linter output into structured diagnostic issues."""
        combined_output = (raw_stderr or "") + "\n" + (raw_stdout or "")
        issues: List[Dict[str, Any]] = []

        for line in combined_output.splitlines():
            line_str = line.strip()
            if not line_str:
                continue

            match = self.DIAGNOSTIC_PATTERN.match(line_str)
            if match:
                file_path = match.group("file").strip()
                line_no = int(match.group("line"))
                col_no = int(match.group("col")) if match.group("col") else None
                raw_severity = (match.group("severity") or "").lower()
                message = match.group("message").strip()

                if "warning" in raw_severity:
                    severity = "warning"
                elif "note" in raw_severity or "info" in raw_severity:
                    severity = "info"
                else:
                    severity = "error"

                # Normalize syntax error descriptions
                if "syntax error" in raw_severity and not message:
                    message = "Syntax error"

                issues.append({
                    "file": file_path,
                    "line": line_no,
                    "column": col_no,
                    "severity": severity,
                    "message": message,
                    "raw": line_str
                })
            elif "syntax error" in line_str.lower() or "error:" in line_str.lower():
                issues.append({
                    "file": None,
                    "line": None,
                    "column": None,
                    "severity": "error",
                    "message": line_str,
                    "raw": line_str
                })
            elif "warning:" in line_str.lower():
                issues.append({
                    "file": None,
                    "line": None,
                    "column": None,
                    "severity": "warning",
                    "message": line_str,
                    "raw": line_str
                })

        return issues

    def _resolve_sources(
        self,
        project_path: str,
        source_files: Optional[List[str]] = None
    ) -> List[str]:
        """
        Resolve source files to inspect. If not specified, discover all RTL files in project.
        """
        if source_files:
            resolved = []
            for sf in source_files:
                full_p = sf if os.path.isabs(sf) else os.path.join(project_path, sf)
                full_p = os.path.abspath(full_p)
                if not os.path.isfile(full_p):
                    raise FileNotFoundError(f"Specified source file not found: {sf}")
                resolved.append(full_p)
            return resolved

        inspector = RTLProjectInspector(project_path)
        inspection = inspector.inspect()
        if not inspection["is_valid"]:
            raise ValueError(inspection.get("error") or "Invalid project path")

        return [f["path"] for f in inspection["files"]]

    def run_lint(
        self,
        project_path: str,
        source_files: Optional[List[str]] = None,
        top_module: Optional[str] = None,
        verilog_standard: str = "2012",
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        Execute RTL syntax check and linting using Icarus Verilog's null target (-t null).
        This elaborates and checks all Verilog syntax and hierarchies without altering files.
        """
        iverilog_info = self.detector.detect_iverilog()
        if not iverilog_info["available"]:
            return {
                "operation": "lint",
                "success": False,
                "error": "Icarus Verilog (iverilog) is not available on this system.",
                "issues": [],
                "raw_stdout": "",
                "raw_stderr": "",
                "execution_time_ms": 0
            }

        iverilog_bin = iverilog_info["executable_path"]
        env = self.detector.get_execution_env(iverilog_bin)

        try:
            sources = self._resolve_sources(project_path, source_files)
        except Exception as e:
            return {
                "operation": "lint",
                "success": False,
                "error": str(e),
                "issues": [],
                "raw_stdout": "",
                "raw_stderr": "",
                "execution_time_ms": 0
            }

        if not sources:
            return {
                "operation": "lint",
                "success": False,
                "error": "No RTL source files to lint.",
                "issues": [],
                "raw_stdout": "",
                "raw_stderr": "",
                "execution_time_ms": 0
            }

        # Build command arguments: -t null (null target), -Wall (all warnings), -g<standard>
        cmd: List[str] = [iverilog_bin, "-t", "null", "-Wall", f"-g{verilog_standard}"]

        if top_module:
            cmd.extend(["-s", top_module])

        # Include project path for `include resolution
        cmd.extend(["-I", os.path.abspath(project_path)])
        cmd.extend(sources)

        start_time = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout,
                cwd=project_path,
                env=env
            )
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            issues = self.parse_diagnostics(proc.stderr, proc.stdout)

            # Success only if returncode == 0 and no error severity issues
            has_errors = any(i["severity"] == "error" for i in issues) or proc.returncode != 0
            success = not has_errors

            return {
                "operation": "lint",
                "success": success,
                "return_code": proc.returncode,
                "tool": "iverilog",
                "tool_version": iverilog_info["version"],
                "sources_analyzed": [os.path.relpath(s, project_path).replace("\\", "/") for s in sources],
                "issues_count": {
                    "errors": sum(1 for i in issues if i["severity"] == "error"),
                    "warnings": sum(1 for i in issues if i["severity"] == "warning"),
                    "info": sum(1 for i in issues if i["severity"] == "info")
                },
                "issues": issues,
                "raw_stdout": proc.stdout,
                "raw_stderr": proc.stderr,
                "execution_time_ms": elapsed_ms,
                "error": None if success else (proc.stderr.strip() or "Linting errors encountered.")
            }
        except subprocess.TimeoutExpired:
            return {
                "operation": "lint",
                "success": False,
                "return_code": -1,
                "tool": "iverilog",
                "tool_version": iverilog_info["version"],
                "sources_analyzed": [os.path.relpath(s, project_path).replace("\\", "/") for s in sources],
                "issues_count": {"errors": 1, "warnings": 0, "info": 0},
                "issues": [{
                    "file": None,
                    "line": None,
                    "column": None,
                    "severity": "error",
                    "message": f"Linting process timed out after {timeout} seconds.",
                    "raw": "Timeout"
                }],
                "raw_stdout": "",
                "raw_stderr": f"Process exceeded timeout limit ({timeout}s)",
                "execution_time_ms": timeout * 1000,
                "error": f"Linting timed out after {timeout} seconds."
            }
        except Exception as e:
            return {
                "operation": "lint",
                "success": False,
                "return_code": -1,
                "tool": "iverilog",
                "tool_version": iverilog_info["version"],
                "sources_analyzed": [],
                "issues_count": {"errors": 1, "warnings": 0, "info": 0},
                "issues": [],
                "raw_stdout": "",
                "raw_stderr": "",
                "execution_time_ms": 0,
                "error": f"Execution failed: {str(e)}"
            }

    def run_compile(
        self,
        project_path: str,
        source_files: Optional[List[str]] = None,
        output_filename: str = "sim_model.vvp",
        top_module: Optional[str] = None,
        verilog_standard: str = "2012",
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        Compile RTL design into an executable VVP simulation target.
        Artifacts are placed in a `.forge_build` subfolder to avoid touching user source files.
        """
        iverilog_info = self.detector.detect_iverilog()
        if not iverilog_info["available"]:
            return {
                "operation": "compile",
                "success": False,
                "error": "Icarus Verilog is not available.",
                "artifact_path": None,
                "issues": [],
                "raw_stdout": "",
                "raw_stderr": "",
                "execution_time_ms": 0
            }

        iverilog_bin = iverilog_info["executable_path"]
        env = self.detector.get_execution_env(iverilog_bin)

        try:
            sources = self._resolve_sources(project_path, source_files)
        except Exception as e:
            return {
                "operation": "compile",
                "success": False,
                "error": str(e),
                "artifact_path": None,
                "issues": [],
                "raw_stdout": "",
                "raw_stderr": "",
                "execution_time_ms": 0
            }

        build_dir = os.path.join(project_path, ".forge_build")
        os.makedirs(build_dir, exist_ok=True)
        out_path = os.path.join(build_dir, output_filename)

        cmd: List[str] = [
            iverilog_bin,
            "-o", out_path,
            "-Wall",
            f"-g{verilog_standard}",
            "-I", os.path.abspath(project_path)
        ]

        if top_module:
            cmd.extend(["-s", top_module])

        cmd.extend(sources)

        start_time = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout,
                cwd=project_path,
                env=env
            )
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            issues = self.parse_diagnostics(proc.stderr, proc.stdout)

            success = proc.returncode == 0 and os.path.isfile(out_path)
            artifact_size = os.path.getsize(out_path) if success else 0

            return {
                "operation": "compile",
                "success": success,
                "return_code": proc.returncode,
                "tool": "iverilog",
                "tool_version": iverilog_info["version"],
                "artifact_path": out_path if success else None,
                "artifact_size_bytes": artifact_size,
                "sources_compiled": [os.path.relpath(s, project_path).replace("\\", "/") for s in sources],
                "issues_count": {
                    "errors": sum(1 for i in issues if i["severity"] == "error"),
                    "warnings": sum(1 for i in issues if i["severity"] == "warning")
                },
                "issues": issues,
                "raw_stdout": proc.stdout,
                "raw_stderr": proc.stderr,
                "execution_time_ms": elapsed_ms,
                "error": None if success else (proc.stderr.strip() or "Compilation failed.")
            }
        except subprocess.TimeoutExpired:
            return {
                "operation": "compile",
                "success": False,
                "return_code": -1,
                "tool": "iverilog",
                "artifact_path": None,
                "issues": [],
                "raw_stdout": "",
                "raw_stderr": f"Compilation timed out after {timeout} seconds.",
                "execution_time_ms": timeout * 1000,
                "error": f"Compilation timed out after {timeout} seconds."
            }

    def run_simulate(
        self,
        project_path: str,
        vvp_file: Optional[str] = None,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        Execute compiled simulation model via `vvp`.
        """
        iverilog_info = self.detector.detect_iverilog()
        vvp_bin = iverilog_info.get("runtime_path")
        if not vvp_bin or not os.path.isfile(vvp_bin):
            return {
                "operation": "simulate",
                "success": False,
                "error": "VVP simulation runtime executable not found.",
                "raw_stdout": "",
                "raw_stderr": "",
                "execution_time_ms": 0
            }

        env = self.detector.get_execution_env(vvp_bin)

        # If vvp_file is not explicitly given, look for default in .forge_build
        target_vvp = vvp_file
        if not target_vvp:
            default_path = os.path.join(project_path, ".forge_build", "sim_model.vvp")
            if os.path.isfile(default_path):
                target_vvp = default_path
            else:
                return {
                    "operation": "simulate",
                    "success": False,
                    "error": "No compiled simulation binary found. Run compile operation first.",
                    "raw_stdout": "",
                    "raw_stderr": "",
                    "execution_time_ms": 0
                }

        cmd = [vvp_bin, target_vvp]

        start_time = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                shell=False,
                timeout=timeout,
                cwd=project_path,
                env=env
            )
            elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
            success = proc.returncode == 0

            # Scan for generated .vcd waveform files
            vcd_files = []
            for root, _, files in os.walk(project_path):
                for f in files:
                    if f.endswith(".vcd"):
                        vcd_files.append(os.path.relpath(os.path.join(root, f), project_path).replace("\\", "/"))

            return {
                "operation": "simulate",
                "success": success,
                "return_code": proc.returncode,
                "tool": "vvp",
                "binary_executed": os.path.relpath(target_vvp, project_path).replace("\\", "/"),
                "waveforms_generated": vcd_files,
                "simulation_log": proc.stdout,
                "raw_stdout": proc.stdout,
                "raw_stderr": proc.stderr,
                "execution_time_ms": elapsed_ms,
                "error": None if success else (proc.stderr.strip() or "Simulation ended with non-zero exit code.")
            }
        except subprocess.TimeoutExpired:
            return {
                "operation": "simulate",
                "success": False,
                "return_code": -1,
                "tool": "vvp",
                "simulation_log": "",
                "raw_stdout": "",
                "raw_stderr": f"Simulation timed out after {timeout} seconds.",
                "execution_time_ms": timeout * 1000,
                "error": f"Simulation exceeded time limit ({timeout}s). Check for infinite loops in testbench."
            }
