"""
Detector module for IC / RTL electronic design automation (EDA) tools.
Responsible for detecting tool presence, resolving paths, and querying version info safely.
"""

import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional


class ICDetector:
    """Detects installed IC/RTL synthesis, simulation, and linting tools."""

    SUPPORTED_TOOLS = ["iverilog", "verilator"]

    def __init__(self, custom_paths: Optional[List[str]] = None):
        """
        Initialize detector with optional custom search paths.
        """
        self.custom_paths = custom_paths or []

    def _get_potential_binary_paths(self, binary_name: str) -> List[str]:
        """Generate candidate locations for the tool executable."""
        candidates: List[str] = []

        # 1. Custom user-provided paths
        for p in self.custom_paths:
            candidate = os.path.join(p, binary_name)
            if os.name == "nt" and not candidate.lower().endswith(".exe"):
                candidate += ".exe"
            candidates.append(candidate)

        # 2. Check system PATH via shutil.which
        found_in_path = shutil.which(binary_name)
        if found_in_path:
            candidates.append(found_in_path)

        # 3. Known common Windows installation directories
        if os.name == "nt":
            user_profile = os.environ.get("USERPROFILE", "")
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            common_roots = [
                r"C:\iverilog\bin",
                r"C:\Program Files\iverilog\bin",
                r"C:\Program Files (x86)\iverilog\bin",
                r"C:\tools\iverilog\bin",
                # Portable scratch/tools location in Antigravity
                os.path.join(user_profile, r".gemini\antigravity\scratch\tools\iverilog\ucrt64\bin"),
                os.path.join(local_appdata, r"Programs\iverilog\bin"),
                os.path.join(local_appdata, r"iverilog\bin"),
            ]
            exe_name = binary_name if binary_name.lower().endswith(".exe") else f"{binary_name}.exe"
            for root in common_roots:
                candidates.append(os.path.join(root, exe_name))

        # 4. Known common Linux/macOS paths
        else:
            common_unix_roots = ["/usr/bin", "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin/verilog"]
            for root in common_unix_roots:
                candidates.append(os.path.join(root, binary_name))

        # Deduplicate while preserving order
        unique_candidates: List[str] = []
        for c in candidates:
            normalized = os.path.normpath(c)
            if normalized not in unique_candidates:
                unique_candidates.append(normalized)

        return unique_candidates

    def find_executable(self, binary_name: str) -> Optional[str]:
        """Find an existing executable binary from candidate paths."""
        candidates = self._get_potential_binary_paths(binary_name)
        for path in candidates:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    def get_execution_env(self, executable_path: Optional[str] = None) -> Dict[str, str]:
        """
        Prepare a sanitized environment containing necessary DLL search paths.
        On Windows, ensure adjacent bin and mingw64/runtime paths are included.
        """
        env = os.environ.copy()
        extra_paths: List[str] = []

        if executable_path:
            exe_dir = os.path.dirname(os.path.abspath(executable_path))
            extra_paths.append(exe_dir)

        if os.name == "nt":
            git_mingw64 = r"C:\Program Files\Git\mingw64\bin"
            if os.path.isdir(git_mingw64):
                extra_paths.append(git_mingw64)

        if extra_paths:
            current_path = env.get("PATH", "")
            env["PATH"] = os.pathsep.join(extra_paths) + os.pathsep + current_path

        return env

    def detect_iverilog(self) -> Dict[str, Any]:
        """Detect Icarus Verilog compiler and runtime."""
        iverilog_path = self.find_executable("iverilog")
        vvp_path = self.find_executable("vvp")

        if not iverilog_path:
            return {
                "tool": "iverilog",
                "available": False,
                "version": None,
                "executable_path": None,
                "runtime_path": None,
                "capabilities": [],
                "error": "Icarus Verilog executable (iverilog) was not found in PATH or standard install paths."
            }

        # Query version safely via subprocess
        env = self.get_execution_env(iverilog_path)
        version_str: Optional[str] = None
        error_msg: Optional[str] = None

        try:
            result = subprocess.run(
                [iverilog_path, "-V"],
                capture_output=True,
                text=True,
                shell=False,
                timeout=10,
                env=env
            )
            # Match: "Icarus Verilog version 13.0 (stable)" or similar
            match = re.search(r"Icarus Verilog version\s+([^\r\n]+)", result.stdout)
            if match:
                version_str = match.group(1).strip()
            elif result.stdout:
                version_str = result.stdout.splitlines()[0].strip()
            else:
                version_str = "Unknown version"
        except subprocess.TimeoutExpired:
            error_msg = "Timeout while querying iverilog version."
        except Exception as e:
            error_msg = f"Failed to execute iverilog: {str(e)}"

        capabilities = ["lint", "syntax_check", "compile"]
        if vvp_path:
            capabilities.append("simulate")

        return {
            "tool": "iverilog",
            "available": version_str is not None,
            "version": version_str,
            "executable_path": iverilog_path,
            "runtime_path": vvp_path,
            "capabilities": capabilities,
            "error": error_msg
        }

    def detect_verilator(self) -> Dict[str, Any]:
        """Detect Verilator linter and simulator."""
        verilator_path = self.find_executable("verilator")
        if not verilator_path:
            return {
                "tool": "verilator",
                "available": False,
                "version": None,
                "executable_path": None,
                "capabilities": [],
                "error": "Verilator executable was not found."
            }

        env = self.get_execution_env(verilator_path)
        version_str = None
        error_msg = None

        try:
            result = subprocess.run(
                [verilator_path, "--version"],
                capture_output=True,
                text=True,
                shell=False,
                timeout=10,
                env=env
            )
            if result.stdout:
                version_str = result.stdout.strip()
        except Exception as e:
            error_msg = str(e)

        return {
            "tool": "verilator",
            "available": version_str is not None,
            "version": version_str,
            "executable_path": verilator_path,
            "capabilities": ["lint", "compile"] if version_str else [],
            "error": error_msg
        }

    def detect_all(self) -> Dict[str, Any]:
        """Detect all supported IC/RTL tools and return a comprehensive status report."""
        iverilog_info = self.detect_iverilog()
        verilator_info = self.detect_verilator()

        primary_tool = None
        if iverilog_info["available"]:
            primary_tool = "iverilog"
        elif verilator_info["available"]:
            primary_tool = "verilator"

        return {
            "primary_tool": primary_tool,
            "tools": {
                "iverilog": iverilog_info,
                "verilator": verilator_info
            }
        }
