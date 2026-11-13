"""
RTL Project Inspector module.
Discovers and statically inspects Verilog/SystemVerilog projects, modules, top candidates, and file trees.
"""

import os
import re
from typing import Any, Dict, List, Optional, Set


class RTLProjectInspector:
    """Inspects RTL/Verilog project structure without modifying files."""

    RTL_EXTENSIONS = {".v", ".sv", ".vh", ".svh"}
    IGNORE_DIRS = {".git", ".svn", "__pycache__", "build", "dist", "node_modules", ".venv", "venv"}

    # Regex patterns for static parsing
    # Matches: module [name] or module [name] #(...) (...) or module [name] (...)
    MODULE_DECL_PATTERN = re.compile(
        r"\bmodule\s+([a-zA-Z_][a-zA-Z0-9_$]*)\s*(?:#\s*\([\s\S]*?\))?\s*(?:\([\s\S]*?\))?\s*;",
        re.MULTILINE
    )
    # Simple module header: module name
    SIMPLE_MODULE_PATTERN = re.compile(
        r"\bmodule\s+([a-zA-Z_][a-zA-Z0-9_$]*)",
        re.MULTILINE
    )
    # Include pattern: `include "filename"
    INCLUDE_PATTERN = re.compile(
        r'^\s*`include\s+["<]([^">]+)[">]',
        re.MULTILINE
    )
    # Simulation indicators (testbench heuristics)
    TB_INDICATORS = re.compile(
        r"(\$dumpfile|\$dumpvars|\$finish|\$stop|\binitial\b)",
        re.MULTILINE
    )

    def __init__(self, project_path: str):
        self.raw_path = project_path
        self.project_path = os.path.abspath(os.path.expanduser(project_path))

    def validate_path(self) -> Dict[str, Any]:
        """Validate that the project path exists, is a directory, and is accessible."""
        if not os.path.exists(self.project_path):
            return {
                "valid": False,
                "error": f"Project path does not exist: {self.project_path}"
            }
        if not os.path.isdir(self.project_path):
            return {
                "valid": False,
                "error": f"Project path is not a directory: {self.project_path}"
            }
        if not os.access(self.project_path, os.R_OK):
            return {
                "valid": False,
                "error": f"Project directory is not readable: {self.project_path}"
            }
        return {"valid": True, "error": None}

    def _strip_comments(self, content: str) -> str:
        """Remove block and line comments to avoid false module matches."""
        # Remove /* block comments */
        content = re.sub(r"/\*[\s\S]*?\*/", "", content)
        # Remove // line comments
        content = re.sub(r"//.*$", "", content, flags=re.MULTILINE)
        return content

    def _parse_file(self, full_path: str, rel_path: str) -> Dict[str, Any]:
        """Parse an individual Verilog/SystemVerilog file for modules and metadata."""
        size = os.path.getsize(full_path)
        lines = 0
        modules: List[str] = []
        includes: List[str] = []
        is_testbench = False

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
                lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)

            clean_content = self._strip_comments(content)

            # Find module declarations
            matches = self.SIMPLE_MODULE_PATTERN.findall(clean_content)
            # Filter out Verilog keywords that might follow module like 'module automatic ...'
            reserved_words = {"automatic", "static"}
            for m in matches:
                if m not in reserved_words and m not in modules:
                    modules.append(m)

            # Find includes
            includes = self.INCLUDE_PATTERN.findall(clean_content)

            # Check for testbench markers or testbench naming
            filename_lower = os.path.basename(full_path).lower()
            if "_tb" in filename_lower or "testbench" in filename_lower:
                is_testbench = True
            elif self.TB_INDICATORS.search(clean_content):
                is_testbench = True

        except Exception as e:
            return {
                "path": full_path,
                "relative_path": rel_path,
                "size_bytes": size,
                "lines": lines,
                "modules": [],
                "includes": [],
                "is_testbench": False,
                "read_error": str(e)
            }

        return {
            "path": full_path,
            "relative_path": rel_path,
            "size_bytes": size,
            "lines": lines,
            "modules": modules,
            "includes": includes,
            "is_testbench": is_testbench,
            "read_error": None
        }

    def inspect(self) -> Dict[str, Any]:
        """
        Inspect project hierarchy, discover all RTL files, extract declared modules,
        and determine top-level candidate modules.
        """
        val = self.validate_path()
        if not val["valid"]:
            return {
                "project_path": self.project_path,
                "is_valid": False,
                "error": val["error"],
                "files": [],
                "modules": [],
                "top_module_candidates": [],
                "warnings": [val["error"]]
            }

        discovered_files: List[Dict[str, Any]] = []
        all_modules: Dict[str, Dict[str, Any]] = {}
        all_declared_module_names: Set[str] = set()

        for root, dirs, files in os.walk(self.project_path):
            # Prune ignored directories in-place
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS and not d.startswith(".")]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in self.RTL_EXTENSIONS:
                    full_file_path = os.path.join(root, file)
                    rel_file_path = os.path.relpath(full_file_path, self.project_path).replace("\\", "/")
                    file_info = self._parse_file(full_file_path, rel_file_path)
                    discovered_files.append(file_info)

                    for mod in file_info["modules"]:
                        all_declared_module_names.add(mod)
                        all_modules[mod] = {
                            "name": mod,
                            "file": rel_file_path,
                            "is_testbench": file_info["is_testbench"],
                            "is_top_candidate": False
                        }

        # Determine module hierarchy / instantiations to find top modules
        instantiated_modules: Set[str] = set()
        for f_info in discovered_files:
            try:
                with open(f_info["path"], "r", encoding="utf-8", errors="ignore") as f:
                    content = self._strip_comments(f.read())
                    # Look for instantiations: <declared_module> <instance_name> (
                    for declared_mod in all_declared_module_names:
                        inst_pattern = rf"\b{re.escape(declared_mod)}\s+(?:#\s*\([\s\S]*?\)\s*)?[a-zA-Z_][a-zA-Z0-9_$]*\s*\("
                        if re.search(inst_pattern, content):
                            # Ensure we don't count module definition itself
                            def_pattern = rf"\bmodule\s+{re.escape(declared_mod)}\b"
                            # Find all matches of inst_pattern and verify it's not the definition
                            for match in re.finditer(inst_pattern, content):
                                start_pos = match.start()
                                prefix = content[:start_pos]
                                if not prefix.rstrip().endswith("module"):
                                    instantiated_modules.add(declared_mod)
                                    break
            except Exception:
                pass

        # Top candidate modules are declared modules that are NOT instantiated anywhere
        top_candidates: List[str] = []
        for mod_name, mod_data in all_modules.items():
            if mod_name not in instantiated_modules:
                mod_data["is_top_candidate"] = True
                top_candidates.append(mod_name)

        # Sort files by relative path
        discovered_files.sort(key=lambda x: x["relative_path"])

        warnings = []
        if not discovered_files:
            warnings.append(f"No RTL source files ({', '.join(sorted(self.RTL_EXTENSIONS))}) found in project.")
        elif not top_candidates:
            warnings.append("Could not automatically determine a top module candidate.")

        return {
            "project_path": self.project_path,
            "is_valid": True,
            "error": None,
            "total_files": len(discovered_files),
            "files": discovered_files,
            "modules": list(all_modules.values()),
            "top_module_candidates": top_candidates,
            "warnings": warnings
        }
