"""Module 3: Tool Registry for the FORGE IT AI Agent Engine.

Maintains a strict registry of approved engineering tools supplied by the
KiCad (PCB) and Verilator/IC (RTL) integration modules.

Security & Integrity Rules:
- Never invent tool functions.
- Validate all tool names against the approved registry.
- Validate all argument names, types, allowed values, and file path safety.
- Block path traversal (`..`), absolute paths outside the project, and shell metacharacters.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .schemas import (
    ApplicationType,
    OperationCategory,
    ToolCallRequest,
    ToolDefinition,
    ToolParameterSchema,
)

# Disallow shell injection characters in file/identifier parameters
_UNSAFE_SHELL_PATTERN = re.compile(r"[;&|`$><\n\r\x00]")


class ToolValidationError(ValueError):
    """Raised when a tool name or its arguments fail registry validation."""


def _validate_safe_relative_path(param_name: str, value: str) -> str:
    """Ensure a file path parameter is a safe relative path inside the project workspace."""
    cleaned = value.strip()
    if not cleaned:
        raise ToolValidationError(f"Parameter '{param_name}' cannot be an empty path.")
    if _UNSAFE_SHELL_PATTERN.search(cleaned):
        raise ToolValidationError(
            f"Parameter '{param_name}' contains unsafe characters: {cleaned!r}"
        )
    # Normalize slashes
    normalized_raw = cleaned.replace("\\", "/")
    if normalized_raw.startswith("/") or re.match(r"^[A-Za-z]:/", normalized_raw):
        raise ToolValidationError(
            f"Parameter '{param_name}' must be a relative project path, not an absolute path: {cleaned!r}"
        )
    parts = [p for p in normalized_raw.split("/") if p and p != "."]
    if ".." in parts:
        raise ToolValidationError(
            f"Path traversal ('..') is forbidden in parameter '{param_name}': {cleaned!r}"
        )
    normalized = posixpath.normpath(normalized_raw)
    if normalized.startswith("..") or normalized.startswith("/"):
        raise ToolValidationError(
            f"Invalid relative path in parameter '{param_name}': {cleaned!r}"
        )
    return normalized


class ToolRegistry:
    """Registry of approved KiCad and RTL engineering tools."""

    def __init__(self, tools: Optional[Iterable[ToolDefinition]] = None) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: ToolDefinition) -> None:
        """Register or update an approved ToolDefinition."""
        if not re.match(r"^[a-z][a-z0-9_]*$", tool.name):
            raise ValueError(
                f"Invalid tool name {tool.name!r}. Must be snake_case alphanumeric."
            )
        self._tools[tool.name] = tool

    def get(self, tool_name: str) -> Optional[ToolDefinition]:
        """Return the ToolDefinition for `tool_name` or None if not registered."""
        return self._tools.get(tool_name)

    def require(self, tool_name: str) -> ToolDefinition:
        """Return the ToolDefinition for `tool_name` or raise ToolValidationError."""
        tool = self._tools.get(tool_name)
        if tool is None:
            approved = ", ".join(sorted(self._tools.keys())) or "none"
            raise ToolValidationError(
                f"Unapproved or unknown tool '{tool_name}'. Approved tools: [{approved}]"
            )
        return tool

    def is_registered(self, tool_name: str) -> bool:
        """Check whether `tool_name` is in the registry."""
        return tool_name in self._tools

    def is_available(self, tool_name: str) -> bool:
        """Check whether `tool_name` is registered AND currently marked available."""
        tool = self._tools.get(tool_name)
        return bool(tool and tool.is_available)

    def set_tool_availability(self, tool_name: str, available: bool) -> None:
        """Update the availability status of a registered tool."""
        tool = self.require(tool_name)
        self._tools[tool_name] = tool.model_copy(update={"is_available": available})

    def sync_availability_from_backend(self, available_tool_names: Iterable[str]) -> None:
        """Sync tool availability based on a list of available tool names from the backend."""
        available_set = set(available_tool_names)
        for name, tool in list(self._tools.items()):
            self._tools[name] = tool.model_copy(
                update={"is_available": name in available_set}
            )

    def list_tools(
        self,
        application: Optional[ApplicationType] = None,
        only_available: bool = False,
    ) -> List[ToolDefinition]:
        """List registered tools, optionally filtered by application and availability."""
        results: List[ToolDefinition] = []
        for tool in self._tools.values():
            if application and tool.application != application:
                continue
            if only_available and not tool.is_available:
                continue
            results.append(tool)
        return results

    def find_tools_by_category(
        self,
        category: OperationCategory,
        application: Optional[ApplicationType] = None,
    ) -> List[ToolDefinition]:
        """Find all registered tools matching an OperationCategory (and optional application)."""
        matches: List[ToolDefinition] = []
        for tool in self._tools.values():
            if tool.category != category:
                continue
            if application and application != ApplicationType.UNKNOWN and tool.application != application:
                continue
            matches.append(tool)
        return matches

    def to_gemini_tools(
        self,
        application: Optional[ApplicationType] = None,
        only_available: bool = False,
    ) -> List[Dict[str, Any]]:
        """Export registered tools as Gemini Interactions API function declarations."""
        return [
            t.to_gemini_function_declaration()
            for t in self.list_tools(application=application, only_available=only_available)
        ]

    def validate_call(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        require_available: bool = True,
    ) -> ToolCallRequest:
        """Validate a tool invocation against the approved tool definition and parameter schemas.

        Returns a sanitized `ToolCallRequest` with defaults populated.
        Raises `ToolValidationError` if the tool is unknown, unavailable, or arguments are invalid.
        """
        tool = self.require(tool_name)
        if require_available and not tool.is_available:
            raise ToolValidationError(
                f"Tool '{tool_name}' is registered but currently unavailable on the backend host."
            )

        raw_args = dict(arguments or {})
        param_map: Dict[str, ToolParameterSchema] = {p.name: p for p in tool.parameters}

        # Reject unknown arguments so the LLM cannot smuggle unexpected flags
        unknown_args = set(raw_args.keys()) - set(param_map.keys())
        if unknown_args:
            allowed_names = ", ".join(sorted(param_map.keys())) or "none"
            raise ToolValidationError(
                f"Unexpected argument(s) {sorted(unknown_args)} for tool '{tool_name}'. "
                f"Allowed parameters: [{allowed_names}]"
            )

        validated_args: Dict[str, Any] = {}
        for param in tool.parameters:
            if param.name not in raw_args or raw_args[param.name] is None:
                if param.required and param.default is None:
                    raise ToolValidationError(
                        f"Missing required argument '{param.name}' for tool '{tool_name}'."
                    )
                if param.default is not None:
                    validated_args[param.name] = param.default
                continue

            val = raw_args[param.name]
            validated_args[param.name] = self._validate_parameter_value(
                tool.name, param, val
            )

        return ToolCallRequest(tool_name=tool.name, arguments=validated_args)

    def _validate_parameter_value(
        self,
        tool_name: str,
        param: ToolParameterSchema,
        value: Any,
    ) -> Any:
        """Validate type, enum constraints, and security rules for a single parameter."""
        expected = param.type
        if expected == "string":
            if not isinstance(value, str):
                raise ToolValidationError(
                    f"Parameter '{param.name}' for tool '{tool_name}' must be a string, got {type(value).__name__}."
                )
            cleaned = value.strip()
            if _UNSAFE_SHELL_PATTERN.search(cleaned):
                raise ToolValidationError(
                    f"Parameter '{param.name}' for tool '{tool_name}' contains forbidden shell metacharacters."
                )
            if param.name.endswith("_file") or param.name.endswith("_path"):
                cleaned = _validate_safe_relative_path(param.name, cleaned)
            value = cleaned

        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolValidationError(
                    f"Parameter '{param.name}' for tool '{tool_name}' must be an integer, got {type(value).__name__}."
                )

        elif expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ToolValidationError(
                    f"Parameter '{param.name}' for tool '{tool_name}' must be a number, got {type(value).__name__}."
                )
            value = float(value)

        elif expected == "boolean":
            if not isinstance(value, bool):
                raise ToolValidationError(
                    f"Parameter '{param.name}' for tool '{tool_name}' must be a boolean, got {type(value).__name__}."
                )

        elif expected == "array":
            if not isinstance(value, list):
                raise ToolValidationError(
                    f"Parameter '{param.name}' for tool '{tool_name}' must be a list/array, got {type(value).__name__}."
                )
            if param.name.endswith("_files") or param.name.endswith("_paths"):
                sanitized_list: List[str] = []
                for item in value:
                    if not isinstance(item, str):
                        raise ToolValidationError(
                            f"Elements of '{param.name}' must be file path strings."
                        )
                    sanitized_list.append(_validate_safe_relative_path(param.name, item))
                value = sanitized_list
            else:
                for item in value:
                    if isinstance(item, str) and _UNSAFE_SHELL_PATTERN.search(item):
                        raise ToolValidationError(
                            f"Element in '{param.name}' contains forbidden shell metacharacters."
                        )

        elif expected == "object":
            if not isinstance(value, dict):
                raise ToolValidationError(
                    f"Parameter '{param.name}' for tool '{tool_name}' must be an object/dict, got {type(value).__name__}."
                )

        if param.allowed_values is not None and value not in param.allowed_values:
            raise ToolValidationError(
                f"Invalid value {value!r} for parameter '{param.name}' of tool '{tool_name}'. "
                f"Allowed values: {param.allowed_values}"
            )

        return value


def build_default_tools() -> List[ToolDefinition]:
    """Return the canonical list of approved KiCad (Member 4) and RTL/IC (Member 5) tools.

    Prioritises a complete working KiCad workflow first, followed by RTL integration.
    """
    return [
        # ------------------------------------------------------------------
        # KiCad (PCB & Schematic) Approved Tools — Member 4 Integration
        # ------------------------------------------------------------------
        ToolDefinition(
            name="kicad_inspect_project",
            application=ApplicationType.KICAD,
            category=OperationCategory.INSPECT_PROJECT,
            description=(
                "Inspect a KiCad project directory to discover schematic (.kicad_sch), "
                "board (.kicad_pcb), project (.kicad_pro) files, layer counts, component counts, and nets."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the uploaded KiCad project.",
                    required=True,
                ),
            ],
            modifies_files=False,
            requires_approval=False,
            is_available=True,
        ),
        ToolDefinition(
            name="kicad_run_drc",
            application=ApplicationType.KICAD,
            category=OperationCategory.DRC_CHECK,
            description=(
                "Run KiCad Design Rule Check (DRC) on a PCB layout (.kicad_pcb) using kicad-cli "
                "and return structured clearance, track width, unconnected net, and courtyard violations."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the KiCad project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="board_file",
                    type="string",
                    description="Relative path to the .kicad_pcb board file inside the project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="severity_filter",
                    type="string",
                    description="Minimum violation severity to include in the report.",
                    required=False,
                    default="all",
                    allowed_values=["all", "error", "warning"],
                ),
                ToolParameterSchema(
                    name="check_unconnected",
                    type="boolean",
                    description="Whether to strictly report unconnected pads/nets.",
                    required=False,
                    default=True,
                ),
            ],
            modifies_files=False,
            requires_approval=False,
            is_available=True,
        ),
        ToolDefinition(
            name="kicad_run_erc",
            application=ApplicationType.KICAD,
            category=OperationCategory.ERC_CHECK,
            description=(
                "Run KiCad Electrical Rules Check (ERC) on a schematic (.kicad_sch) using kicad-cli "
                "to detect pin conflicts, missing power flags, and floating pins."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the KiCad project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="schematic_file",
                    type="string",
                    description="Relative path to the root .kicad_sch schematic file.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="severity_filter",
                    type="string",
                    description="Severity filter for ERC violations.",
                    required=False,
                    default="all",
                    allowed_values=["all", "error", "warning"],
                ),
            ],
            modifies_files=False,
            requires_approval=False,
            is_available=True,
        ),
        ToolDefinition(
            name="kicad_export_bom",
            application=ApplicationType.KICAD,
            category=OperationCategory.EXPORT_BOM,
            description=(
                "Extract and export a structured Bill of Materials (BOM) from a KiCad schematic (.kicad_sch)."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the KiCad project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="schematic_file",
                    type="string",
                    description="Relative path to the .kicad_sch file.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="output_format",
                    type="string",
                    description="Output format for the generated BOM.",
                    required=False,
                    default="csv",
                    allowed_values=["csv", "json", "xml"],
                ),
                ToolParameterSchema(
                    name="group_by_value",
                    type="boolean",
                    description="Group identical components by value and footprint.",
                    required=False,
                    default=True,
                ),
            ],
            modifies_files=False,
            requires_approval=False,
            is_available=True,
        ),
        ToolDefinition(
            name="kicad_export_netlist",
            application=ApplicationType.KICAD,
            category=OperationCategory.EXPORT_NETLIST,
            description="Export connectivity netlist from a KiCad schematic (.kicad_sch).",
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the KiCad project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="schematic_file",
                    type="string",
                    description="Relative path to the .kicad_sch file.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="output_format",
                    type="string",
                    description="Netlist format.",
                    required=False,
                    default="kicadsexpr",
                    allowed_values=["kicadsexpr", "kicadxml", "spice"],
                ),
            ],
            modifies_files=False,
            requires_approval=False,
            is_available=True,
        ),
        ToolDefinition(
            name="kicad_export_gerbers",
            application=ApplicationType.KICAD,
            category=OperationCategory.EXPORT_GERBER,
            description="Generate fabrication Gerber and drill artifacts from a KiCad PCB (.kicad_pcb).",
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the KiCad project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="board_file",
                    type="string",
                    description="Relative path to the .kicad_pcb file.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="include_drill",
                    type="boolean",
                    description="Include Excellon drill files alongside copper/mask/silkscreen layers.",
                    required=False,
                    default=True,
                ),
            ],
            modifies_files=False,
            requires_approval=False,
            is_available=True,
        ),
        ToolDefinition(
            name="kicad_update_netclass_design_rules",
            application=ApplicationType.KICAD,
            category=OperationCategory.MODIFY_PCB_PARAMETER,
            description=(
                "Update netclass clearance, track width, or via dimensions in a KiCad project (.kicad_pro / .kicad_pcb). "
                "Modifies project files and requires explicit user approval before execution."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the KiCad project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="board_file",
                    type="string",
                    description="Relative path to the target .kicad_pcb or .kicad_pro file.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="netclass_name",
                    type="string",
                    description="Name of the netclass to update (e.g. 'Default', 'Power').",
                    required=False,
                    default="Default",
                ),
                ToolParameterSchema(
                    name="track_width_mm",
                    type="number",
                    description="New track width in millimeters (e.g. 0.25).",
                    required=False,
                ),
                ToolParameterSchema(
                    name="clearance_mm",
                    type="number",
                    description="New minimum copper clearance in millimeters (e.g. 0.20).",
                    required=False,
                ),
            ],
            modifies_files=True,
            requires_approval=True,
            is_available=True,
        ),
        ToolDefinition(
            name="kicad_update_schematic_component",
            application=ApplicationType.KICAD,
            category=OperationCategory.MODIFY_SCHEMATIC_COMPONENT,
            description=(
                "Edit a component's value or footprint in a KiCad schematic (.kicad_sch), "
                "such as changing resistor R1 from 10k to 4.7k. Modifies project files and requires user approval."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the KiCad project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="schematic_file",
                    type="string",
                    description="Relative path to the target .kicad_sch file.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="reference",
                    type="string",
                    description="Component reference designator (e.g. 'R1', 'C3', 'U1').",
                    required=True,
                ),
                ToolParameterSchema(
                    name="new_value",
                    type="string",
                    description="New component value (e.g. '4.7k', '100nF', 'STM32F405').",
                    required=False,
                ),
                ToolParameterSchema(
                    name="new_footprint",
                    type="string",
                    description="Optional updated KiCad footprint identifier (e.g. 'Resistor_SMD:R_0603_1608Metric').",
                    required=False,
                ),
            ],
            modifies_files=True,
            requires_approval=True,
            is_available=True,
        ),
        # ------------------------------------------------------------------
        # RTL / IC (Verilog / SystemVerilog / Verilator) Approved Tools — Member 5
        # ------------------------------------------------------------------
        ToolDefinition(
            name="rtl_inspect_project",
            application=ApplicationType.RTL,
            category=OperationCategory.INSPECT_PROJECT,
            description=(
                "Inspect an RTL/Verilog/SystemVerilog project to discover modules, ports, "
                "parameters, and testbench files."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the RTL project.",
                    required=True,
                ),
            ],
            modifies_files=False,
            requires_approval=False,
            is_available=True,
        ),
        ToolDefinition(
            name="rtl_run_lint",
            application=ApplicationType.RTL,
            category=OperationCategory.RTL_LINT,
            description=(
                "Run static RTL lint check using Verilator (--lint-only -Wall) on Verilog/SystemVerilog "
                "source files to detect width mismatches, inferred latches, undriven nets, and syntax errors."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the RTL project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="source_files",
                    type="array",
                    description="List of relative paths to .v / .sv RTL source files.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="top_module",
                    type="string",
                    description="Name of the top-level Verilog module (optional if single module).",
                    required=False,
                ),
                ToolParameterSchema(
                    name="wall",
                    type="boolean",
                    description="Enable all stylistic and width warnings (-Wall).",
                    required=False,
                    default=True,
                ),
            ],
            modifies_files=False,
            requires_approval=False,
            is_available=True,
        ),
        ToolDefinition(
            name="rtl_run_simulation",
            application=ApplicationType.RTL,
            category=OperationCategory.RTL_SIMULATE,
            description=(
                "Compile and run an RTL simulation testbench using Verilator/Icarus and capture "
                "assertion results, waveform summary, and simulation logs."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the RTL project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="source_files",
                    type="array",
                    description="List of relative paths to RTL and testbench files.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="top_module",
                    type="string",
                    description="Top-level testbench or design module name.",
                    required=True,
                ),
            ],
            modifies_files=False,
            requires_approval=False,
            is_available=True,
        ),
        ToolDefinition(
            name="rtl_generate_module",
            application=ApplicationType.RTL,
            category=OperationCategory.GENERATE_RTL_MODULE,
            description=(
                "Generate a new synthesizable Verilog/SystemVerilog module or testbench file in the project. "
                "Modifies project files and requires explicit user approval before writing to disk."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the RTL project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="target_file",
                    type="string",
                    description="Relative path for the generated .v or .sv file (e.g. 'rtl/counter.v').",
                    required=True,
                ),
                ToolParameterSchema(
                    name="module_name",
                    type="string",
                    description="Name of the Verilog/SystemVerilog module to generate.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="generated_code",
                    type="string",
                    description="Complete synthesizable Verilog/SystemVerilog source code.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="specification_summary",
                    type="string",
                    description="Summary of the hardware specification implemented by the module.",
                    required=True,
                ),
            ],
            modifies_files=True,
            requires_approval=True,
            is_available=True,
        ),
        ToolDefinition(
            name="rtl_apply_patch",
            application=ApplicationType.RTL,
            category=OperationCategory.MODIFY_RTL_SOURCE,
            description=(
                "Apply an approved RTL source modification to a Verilog/SystemVerilog file in the project. "
                "Modifies project files and requires explicit user approval before execution."
            ),
            parameters=[
                ToolParameterSchema(
                    name="project_id",
                    type="string",
                    description="Unique identifier of the RTL project.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="target_file",
                    type="string",
                    description="Relative path to the .v or .sv file to modify.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="updated_content",
                    type="string",
                    description="Updated Verilog/SystemVerilog source content or patch block.",
                    required=True,
                ),
                ToolParameterSchema(
                    name="change_description",
                    type="string",
                    description="Human-readable summary of the RTL modification.",
                    required=True,
                ),
            ],
            modifies_files=True,
            requires_approval=True,
            is_available=True,
        ),
    ]


def get_default_tool_registry() -> ToolRegistry:
    """Create a fresh ToolRegistry pre-populated with all approved KiCad and RTL tools."""
    return ToolRegistry(build_default_tools())
