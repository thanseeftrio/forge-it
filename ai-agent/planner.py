"""Modules 1 & 2: Request Understanding and Planning Engine for FORGE IT.

Module 1 (`RequestUnderstandingEngine`):
- Understands natural-language engineering requests (via Gemini structured output + deterministic validation/fallback).
- Identifies application (`kicad`, `rtl`, `unknown`), project, target files, intent, and requested operations.
- Detects unsupported operations (e.g. interactive PCB routing, 3D CAD, arbitrary shell execution).
- Asks for clarification when necessary.

Module 2 (`PlanningEngine`):
- Converts `RequestUnderstanding` into a step-by-step `ExecutionPlan`.
- Selects ONLY approved tools from `ToolRegistry`.
- Checks whether required tools are registered and available.
- Never claims an operation is possible unless an approved, available tool supports it.
- Marks file-modifying steps with `requires_approval=True` and `approval_status=PENDING_APPROVAL`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .prompts import (
    REQUEST_UNDERSTANDING_SYSTEM_PROMPT,
    build_understanding_user_prompt,
)
from .provider import GeminiProvider, LLMProvider
from .schemas import (
    ApplicationType,
    ApprovalStatus,
    ExecutionPlan,
    OperationCategory,
    PlanStep,
    PlanValidationStatus,
    ProjectContext,
    RequestUnderstanding,
    StepExecutionStatus,
    ToolDefinition,
)
from .tool_registry import ToolRegistry, ToolValidationError, get_default_tool_registry

# Patterns for explicit file references in user queries
_FILE_REF_PATTERN = re.compile(
    r"\b([A-Za-z0-9_\-./]+\.(?:kicad_pcb|kicad_sch|kicad_pro|v|sv|vh))\b",
    re.IGNORECASE,
)

# Keywords indicating unsupported operations (FORGE IT is an integration layer, not a full editor/shell)
_UNSUPPORTED_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\b(autoroute|auto-route|route\s+all\s+traces|interactive\s+routing)\b", re.I),
        "FORGE IT is an AI integration and analysis layer, not an interactive PCB router. Use KiCad's interactive router or FreeRouting for trace routing, then run DRC through FORGE IT.",
    ),
    (
        re.compile(r"\b(gdsii|place\s+and\s+route\s+asic|openroad\s+floorplan|analog\s+spice\s+layout)\b", re.I),
        "Physical IC floorplanning/GDSII layout editing is not supported by the approved tool registry. FORGE IT supports RTL inspection, Verilator linting, simulation, and approved RTL patches.",
    ),
    (
        re.compile(r"\b(3d\s+model|step\s+export\s+cad|solidworks|freecad|enclosure\s+design)\b", re.I),
        "Mechanical 3D CAD editing is not supported by FORGE IT's approved KiCad and RTL tools.",
    ),
    (
        re.compile(r"\b(rm\s+-rf|sudo\s+|bash\s+-c|curl\s+http|wget\s+http|/bin/sh)\b", re.I),
        "Executing arbitrary shell commands is strictly prohibited by FORGE IT security policy.",
    ),
]


class RequestUnderstandingEngine:
    """Module 1: Understand natural-language engineering requests into structured JSON."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        provider: Optional[LLMProvider] = None,
    ) -> None:
        self.registry = registry or get_default_tool_registry()
        self.provider: LLMProvider = provider or GeminiProvider()

    def understand(
        self,
        query: str,
        project_context: Optional[ProjectContext] = None,
    ) -> RequestUnderstanding:
        """Analyze a natural-language engineering request and return a `RequestUnderstanding`."""
        ctx = project_context or ProjectContext()
        cleaned_query = query.strip()

        # 1. Check hard security / unsupported operation boundaries first
        for pattern, reason in _UNSUPPORTED_PATTERNS:
            if pattern.search(cleaned_query):
                app = self._infer_application(cleaned_query, ctx)
                return RequestUnderstanding(
                    raw_query=cleaned_query,
                    application=app,
                    project_id=ctx.project_id,
                    target_files=self._extract_target_files(cleaned_query, ctx),
                    intent_summary="Requested an operation outside FORGE IT's approved engineering tools.",
                    requested_operations=[OperationCategory.UNSUPPORTED],
                    modifies_files=False,
                    needs_clarification=False,
                    unsupported_reason=reason,
                    confidence=0.98,
                )

        # 2. Try Gemini structured output when live API or mock response is configured
        if self.provider.is_live_api_configured:
            prompt = build_understanding_user_prompt(
                query=cleaned_query,
                project_context=ctx,
                approved_tools=self.registry.list_tools(),
            )
            llm_result = self.provider.generate_structured(
                system_instruction=REQUEST_UNDERSTANDING_SYSTEM_PROMPT,
                user_prompt=prompt,
                response_model=RequestUnderstanding,
                temperature=0.1,
            )
            if llm_result is not None:
                return self._normalize_and_validate_understanding(
                    llm_result, cleaned_query, ctx
                )

        # 3. Deterministic engineering parser (fast, reliable fallback & baseline)
        return self._deterministic_understand(cleaned_query, ctx)

    def _normalize_and_validate_understanding(
        self,
        understanding: RequestUnderstanding,
        raw_query: str,
        ctx: ProjectContext,
    ) -> RequestUnderstanding:
        """Enforce project context defaults, file modification flags, and clarification checks."""
        project_id = understanding.project_id or ctx.project_id
        target_files = list(understanding.target_files)
        if not target_files:
            target_files = self._extract_target_files(raw_query, ctx)

        app = understanding.application
        if app == ApplicationType.UNKNOWN:
            app = self._infer_application(raw_query, ctx, target_files)

        ops = list(understanding.requested_operations)
        if not ops:
            ops = self._infer_operations(raw_query, app)

        modifies = understanding.modifies_files or any(
            op
            in (
                OperationCategory.MODIFY_PCB_PARAMETER,
                OperationCategory.MODIFY_SCHEMATIC_COMPONENT,
                OperationCategory.GENERATE_RTL_MODULE,
                OperationCategory.MODIFY_RTL_SOURCE,
            )
            for op in ops
        )

        extracted_params = dict(understanding.extracted_parameters)
        heuristics_params = self._extract_parameters(raw_query, ctx)
        for k, v in heuristics_params.items():
            extracted_params.setdefault(k, v)

        needs_clarification = understanding.needs_clarification
        questions = list(understanding.clarification_questions)

        # Check if we have enough context to run tool operations
        clarify_flag, auto_questions = self._check_clarification_needed(
            app=app,
            ops=ops,
            project_id=project_id,
            target_files=target_files,
            extracted_params=extracted_params,
            ctx=ctx,
        )
        if clarify_flag:
            needs_clarification = True
            for q in auto_questions:
                if q not in questions:
                    questions.append(q)

        return understanding.model_copy(
            update={
                "raw_query": raw_query,
                "application": app,
                "project_id": project_id,
                "target_files": target_files,
                "requested_operations": ops,
                "extracted_parameters": extracted_params,
                "modifies_files": modifies,
                "needs_clarification": needs_clarification,
                "clarification_questions": questions,
            }
        )

    def _deterministic_understand(
        self,
        query: str,
        ctx: ProjectContext,
    ) -> RequestUnderstanding:
        """Deterministic rule-based understanding for KiCad and RTL engineering requests."""
        target_files = self._extract_target_files(query, ctx)
        app = self._infer_application(query, ctx, target_files)
        ops = self._infer_operations(query, app)
        extracted_params = self._extract_parameters(query, ctx)
        project_id = ctx.project_id or extracted_params.get("project_id")

        modifies_files = any(
            op
            in (
                OperationCategory.MODIFY_PCB_PARAMETER,
                OperationCategory.MODIFY_SCHEMATIC_COMPONENT,
                OperationCategory.GENERATE_RTL_MODULE,
                OperationCategory.MODIFY_RTL_SOURCE,
            )
            for op in ops
        )

        needs_clarification, questions = self._check_clarification_needed(
            app=app,
            ops=ops,
            project_id=project_id,
            target_files=target_files,
            extracted_params=extracted_params,
            ctx=ctx,
        )

        intent_summary = self._build_intent_summary(app, ops, target_files, project_id)

        return RequestUnderstanding(
            raw_query=query,
            application=app,
            project_id=project_id,
            target_files=target_files,
            intent_summary=intent_summary,
            requested_operations=ops,
            extracted_parameters=extracted_params,
            modifies_files=modifies_files,
            needs_clarification=needs_clarification,
            clarification_questions=questions,
            unsupported_reason=None,
            confidence=0.92 if not needs_clarification else 0.55,
        )

    def _infer_application(
        self,
        query: str,
        ctx: ProjectContext,
        target_files: Optional[List[str]] = None,
    ) -> ApplicationType:
        q = query.lower()
        files = (target_files or []) + list(ctx.files)

        # Check explicit keywords in query first
        kicad_keywords = (
            "kicad",
            "pcb",
            "drc",
            "erc",
            "schematic",
            "gerber",
            "bom",
            "bill of materials",
            "netlist",
            "netclass",
            "track width",
            "clearance",
            "footprint",
            "courtyard",
            "silkscreen",
            "solder mask",
            ".kicad_pcb",
            ".kicad_sch",
        )
        rtl_keywords = (
            "verilog",
            "systemverilog",
            "rtl",
            "verilator",
            "testbench",
            "lint",
            "simulate",
            "simulation",
            "always_ff",
            "always @",
            "posedge",
            "negedge",
            "latch",
            "flip-flop",
            "fsm",
            ".v",
            ".sv",
        )

        has_kicad = any(kw in q for kw in kicad_keywords)
        has_rtl = any(kw in q for kw in rtl_keywords)

        if has_kicad and not has_rtl:
            return ApplicationType.KICAD
        if has_rtl and not has_kicad:
            return ApplicationType.RTL

        # Check target or context file extensions
        for f in files:
            fl = f.lower()
            if fl.endswith((".kicad_pcb", ".kicad_sch", ".kicad_pro")):
                return ApplicationType.KICAD
            if fl.endswith((".v", ".sv", ".vh")):
                return ApplicationType.RTL

        if ctx.application and ctx.application != ApplicationType.UNKNOWN:
            return ctx.application

        return ApplicationType.UNKNOWN

    def _infer_operations(
        self,
        query: str,
        app: ApplicationType,
    ) -> List[OperationCategory]:
        q = query.lower()
        ops: List[OperationCategory] = []

        # KiCad operations
        if re.search(r"\b(drc|design\s+rule\s+check|board\s+check|clearance\s+check|pcb\s+errors?)\b", q):
            ops.append(OperationCategory.DRC_CHECK)
        if re.search(r"\b(erc|electrical\s+rules?\s+check|schematic\s+check|schematic\s+errors?|floating\s+pins?)\b", q):
            ops.append(OperationCategory.ERC_CHECK)
        if re.search(r"\b(bom|bill\s+of\s+materials|component\s+list|part\s+list)\b", q):
            ops.append(OperationCategory.EXPORT_BOM)
        if re.search(r"\b(netlist|export\s+nets)\b", q):
            ops.append(OperationCategory.EXPORT_NETLIST)
        if re.search(r"\b(gerbers?|fabrication\s+files?|drill\s+files?)\b", q):
            ops.append(OperationCategory.EXPORT_GERBER)
        if re.search(
            r"\b(update|change|modify|set|increase|decrease)\b.*\b(track\s+width|clearance|netclass|design\s+rules?)\b",
            q,
        ):
            ops.append(OperationCategory.MODIFY_PCB_PARAMETER)
        if re.search(
            r"\b(update|change|modify|set|replace|edit)\b.*\b(resistor|capacitor|inductor|component|footprint|value|[rculj][0-9]{1,4})\b",
            q,
        ) and app != ApplicationType.RTL:
            ops.append(OperationCategory.MODIFY_SCHEMATIC_COMPONENT)

        # RTL operations
        if re.search(r"\b(lint|verilator|syntax\s+check|width\s+mismatch|static\s+check|check\s+rtl|check\s+verilog)\b", q):
            ops.append(OperationCategory.RTL_LINT)
        if re.search(r"\b(simulat(?:e|ion)|run\s+testbench|run\s+tb|waveform)\b", q):
            ops.append(OperationCategory.RTL_SIMULATE)
        if re.search(
            r"\b(generate|create|write|build|synthesize\s+new)\b.*\b(module|verilog|systemverilog|rtl|counter|alu|fsm|uart|fifo|spi|testbench)\b",
            q,
        ):
            ops.append(OperationCategory.GENERATE_RTL_MODULE)
        if re.search(
            r"\b(patch|modify|update|edit|rewrite|apply\s+fix|fix\s+the\s+file)\b.*\b(rtl|verilog|module|\.v|\.sv|code)\b",
            q,
        ):
            ops.append(OperationCategory.MODIFY_RTL_SOURCE)

        # Inspection requests
        if re.search(r"\b(inspect|scan|overview|structure|list\s+files|what\s+files|analyze\s+project)\b", q):
            if OperationCategory.INSPECT_PROJECT not in ops:
                ops.insert(0, OperationCategory.INSPECT_PROJECT)

        # Broad "check my board / project" requests
        if not ops:
            if app == ApplicationType.KICAD and re.search(r"\b(check|verify|validate|analyze|run)\b", q):
                if "schematic" in q or ".kicad_sch" in q:
                    ops.append(OperationCategory.ERC_CHECK)
                elif "board" in q or "pcb" in q or ".kicad_pcb" in q:
                    ops.append(OperationCategory.DRC_CHECK)
                else:
                    ops.extend([OperationCategory.INSPECT_PROJECT, OperationCategory.DRC_CHECK, OperationCategory.ERC_CHECK])
            elif app == ApplicationType.RTL and re.search(r"\b(check|verify|validate|analyze|run)\b", q):
                ops.append(OperationCategory.RTL_LINT)
            elif re.search(r"\b(what\s+is|explain|how\s+does|why\s+does|difference\s+between)\b", q):
                ops.append(OperationCategory.EXPLAIN_CONCEPT)

        return ops

    def _extract_target_files(
        self,
        query: str,
        ctx: ProjectContext,
    ) -> List[str]:
        found: List[str] = []
        for match in _FILE_REF_PATTERN.finditer(query):
            candidate = match.group(1).strip()
            # Reject path traversal in extracted filenames
            if ".." not in candidate and not candidate.startswith("/"):
                if candidate not in found:
                    found.append(candidate)

        # Also pull from project context if not explicitly in the query
        for f in ctx.files:
            if ".." not in f and not f.startswith("/") and f not in found:
                found.append(f)

        return found

    def _extract_parameters(
        self,
        query: str,
        ctx: ProjectContext,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        q = query.lower()

        if ctx.project_id:
            params["project_id"] = ctx.project_id

        # Extract severity filter
        if "errors only" in q or "only errors" in q:
            params["severity_filter"] = "error"
        elif "warnings only" in q:
            params["severity_filter"] = "warning"

        # Extract BOM / Netlist format
        if "json" in q:
            params["output_format"] = "json"
        elif "xml" in q:
            params["output_format"] = "xml"
        elif "spice" in q:
            params["output_format"] = "spice"
        elif "csv" in q:
            params["output_format"] = "csv"

        # Extract track width / clearance in mm (e.g., "track width to 0.3 mm", "clearance to 0.25mm")
        tw_match = re.search(r"track\s+width\s*(?:to|=|:)?\s*([0-9]+(?:\.[0-9]+)?)\s*mm", q)
        if tw_match:
            params["track_width_mm"] = float(tw_match.group(1))

        clr_match = re.search(r"clearance\s*(?:to|=|:)?\s*([0-9]+(?:\.[0-9]+)?)\s*mm", q)
        if clr_match:
            params["clearance_mm"] = float(clr_match.group(1))

        nc_match = re.search(r"netclass\s+['\"]?([A-Za-z0-9_\-]+)['\"]?", query, re.I)
        if nc_match:
            params["netclass_name"] = nc_match.group(1)

        # Extract schematic component reference & new value (e.g., "change R1 value to 4.7k", "set C2 to 100nF")
        comp_match = re.search(
            r"\b([RCULJDQ][0-9]{1,4})\b.*?(?:to|=)\s*([0-9]+(?:\.[0-9]+)?\s*[kKmMuUnNpP]?[FHRΩhms]*|[A-Za-z0-9_\-]+)",
            query,
        )
        if comp_match:
            params["reference"] = comp_match.group(1).upper()
            params["new_value"] = comp_match.group(2).strip().rstrip(".")

        fp_match = re.search(r"footprint\s*(?:to|=|:)?\s*['\"]?([A-Za-z0-9_:\-.]+)['\"]?", query, re.I)
        if fp_match:
            params["new_footprint"] = fp_match.group(1)

        # Extract RTL module generation parameters
        mod_match = re.search(r"module\s+(?:named\s+|called\s+)?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", query, re.I)
        if mod_match and mod_match.group(1).lower() not in ("in", "on", "for", "with", "to", "from"):
            params.setdefault("module_name", mod_match.group(1))

        if re.search(r"\b(generate|create|write|build)\b.*\b(module|verilog|systemverilog|rtl|counter|alu|fsm|uart|fifo)\b", q):
            rtl_targets = [f for f in self._extract_target_files(query, ctx) if f.lower().endswith((".v", ".sv"))]
            if rtl_targets:
                params.setdefault("target_file", rtl_targets[0])
                stem = rtl_targets[0].rsplit("/", 1)[-1].rsplit(".", 1)[0]
                params.setdefault("module_name", stem)
            else:
                params.setdefault("module_name", "generated_module")
                params.setdefault("target_file", f"rtl/{params['module_name']}.v")

            mod_name = params["module_name"]
            params.setdefault("specification_summary", query.strip())
            params.setdefault(
                "generated_code",
                self._synthesize_verilog_template(mod_name, query),
            )

        # Extract top_module for RTL
        top_match = re.search(r"top(?:\s+module|_module)\s+['\"]?([A-Za-z_][A-Za-z0-9_$]*)['\"]?", query, re.I)
        if top_match:
            params["top_module"] = top_match.group(1)
        elif "top_module" in ctx.metadata:
            params["top_module"] = ctx.metadata["top_module"]

        # Copy metadata hints
        if "board_file" in ctx.metadata:
            params.setdefault("board_file", ctx.metadata["board_file"])
        if "schematic_file" in ctx.metadata:
            params.setdefault("schematic_file", ctx.metadata["schematic_file"])

        return params

    def _check_clarification_needed(
        self,
        *,
        app: ApplicationType,
        ops: List[OperationCategory],
        project_id: Optional[str],
        target_files: List[str],
        extracted_params: Dict[str, Any],
        ctx: ProjectContext,
    ) -> Tuple[bool, List[str]]:
        questions: List[str] = []

        if OperationCategory.UNSUPPORTED in ops:
            return False, []

        if ops == [OperationCategory.EXPLAIN_CONCEPT]:
            return False, []

        if app == ApplicationType.UNKNOWN or not ops:
            questions.append(
                "Which engineering workflow would you like to run (e.g., KiCad DRC/ERC/BOM check or Verilog/RTL lint & simulation)?"
            )
            if not project_id:
                questions.append("Which project would you like me to analyze?")
            return True, questions

        if not project_id:
            questions.append(
                f"Please select or upload a {app.value.upper()} project so I can run this operation."
            )

        # Check file requirements for KiCad operations
        board_files = [f for f in target_files if f.lower().endswith(".kicad_pcb")]
        sch_files = [f for f in target_files if f.lower().endswith(".kicad_sch")]
        rtl_files = [f for f in target_files if f.lower().endswith((".v", ".sv"))]

        needs_board = any(
            op in (OperationCategory.DRC_CHECK, OperationCategory.EXPORT_GERBER, OperationCategory.MODIFY_PCB_PARAMETER)
            for op in ops
        )
        needs_sch = any(
            op
            in (
                OperationCategory.ERC_CHECK,
                OperationCategory.EXPORT_BOM,
                OperationCategory.EXPORT_NETLIST,
                OperationCategory.MODIFY_SCHEMATIC_COMPONENT,
            )
            for op in ops
        )

        if needs_board and not board_files and "board_file" not in extracted_params:
            questions.append(
                "Which KiCad PCB file (`.kicad_pcb`) should I use for this operation?"
            )

        if needs_sch and not sch_files and "schematic_file" not in extracted_params:
            questions.append(
                "Which KiCad schematic file (`.kicad_sch`) should I use for this operation?"
            )

        if app == ApplicationType.RTL and any(
            op in (OperationCategory.RTL_LINT, OperationCategory.RTL_SIMULATE, OperationCategory.MODIFY_RTL_SOURCE)
            for op in ops
        ):
            if not rtl_files and not extracted_params.get("source_files"):
                questions.append(
                    "Which Verilog/SystemVerilog source file(s) (`.v` / `.sv`) should I run this on?"
                )
            if OperationCategory.RTL_SIMULATE in ops and not extracted_params.get("top_module"):
                questions.append(
                    "What is the name of the top-level testbench or design module (`top_module`) to simulate?"
                )

        if OperationCategory.MODIFY_PCB_PARAMETER in ops:
            if "track_width_mm" not in extracted_params and "clearance_mm" not in extracted_params:
                questions.append(
                    "What new `track_width_mm` or `clearance_mm` value (in millimeters) should I apply to the netclass?"
                )

        if OperationCategory.MODIFY_SCHEMATIC_COMPONENT in ops:
            if not extracted_params.get("reference"):
                questions.append(
                    "Which schematic component reference designator (e.g., `R1`, `C2`, `U1`) should I update?"
                )
            if not extracted_params.get("new_value") and not extracted_params.get("new_footprint"):
                questions.append(
                    "What new value (`new_value`) or footprint (`new_footprint`) should I set on the component?"
                )

        if OperationCategory.MODIFY_RTL_SOURCE in ops:
            if not extracted_params.get("updated_content"):
                questions.append(
                    "Please provide or confirm the updated RTL source code (`updated_content`) you want applied."
                )

        return (len(questions) > 0), questions

    def _synthesize_verilog_template(self, module_name: str, query: str) -> str:
        """Synthesize a clean, lint-clean Verilog module template matching the user's specification."""
        q = query.lower()
        bit_match = re.search(r"\b(\d+)\s*-\s*bit\b", q)
        width = int(bit_match.group(1)) if bit_match else 8
        msb = max(width - 1, 0)

        if "counter" in q:
            return (
                f"module {module_name} #(\n"
                f"    parameter int WIDTH = {width}\n"
                f") (\n"
                f"    input  wire             clk,\n"
                f"    input  wire             rst_n,\n"
                f"    input  wire             en,\n"
                f"    output reg  [WIDTH-1:0] count\n"
                f");\n"
                f"    always @(posedge clk or negedge rst_n) begin\n"
                f"        if (!rst_n) begin\n"
                f"            count <= {{WIDTH{{1'b0}}}};\n"
                f"        end else if (en) begin\n"
                f"            count <= count + {{{{WIDTH-1{{1'b0}}}}, 1'b1}};\n"
                f"        end\n"
                f"    end\n"
                f"endmodule\n"
            )

        return (
            f"module {module_name} (\n"
            f"    input  wire        clk,\n"
            f"    input  wire        rst_n,\n"
            f"    input  wire [{msb}:0] data_in,\n"
            f"    output reg  [{msb}:0] data_out\n"
            f");\n"
            f"    always @(posedge clk or negedge rst_n) begin\n"
            f"        if (!rst_n) begin\n"
            f"            data_out <= {width}'d0;\n"
            f"        end else begin\n"
            f"            data_out <= data_in;\n"
            f"        end\n"
            f"    end\n"
            f"endmodule\n"
        )

    def _build_intent_summary(
        self,
        app: ApplicationType,
        ops: List[OperationCategory],
        target_files: List[str],
        project_id: Optional[str],
    ) -> str:
        if not ops:
            return "Clarify the user's engineering request and target project."
        op_names = ", ".join(op.value for op in ops)
        files_desc = f" on {', '.join(target_files[:3])}" if target_files else ""
        proj_desc = f" in project '{project_id}'" if project_id else ""
        return f"Execute [{op_names}] for {app.value.upper()}{files_desc}{proj_desc}."


class PlanningEngine:
    """Module 2: Convert structured requests into step-by-step ExecutionPlans."""

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        self.registry = registry or get_default_tool_registry()

    def create_plan(
        self,
        understanding: RequestUnderstanding,
        project_context: Optional[ProjectContext] = None,
    ) -> ExecutionPlan:
        """Generate a validated, step-by-step `ExecutionPlan` from a `RequestUnderstanding`."""
        ctx = project_context or ProjectContext()

        # Sync tool availability if project_context specifies available_tools
        if ctx.available_tools is not None:
            self.registry.sync_availability_from_backend(ctx.available_tools)

        # Case 1: Unsupported operation
        if (
            OperationCategory.UNSUPPORTED in understanding.requested_operations
            or understanding.unsupported_reason
        ):
            reason = (
                understanding.unsupported_reason
                or "The requested operation is not supported by any approved tool in the FORGE IT Tool Registry."
            )
            return ExecutionPlan(
                request_id=understanding.request_id,
                application=understanding.application,
                project_id=understanding.project_id,
                summary=reason,
                validation_status=PlanValidationStatus.UNSUPPORTED_OPERATION,
                steps=[],
                requires_user_approval=False,
                missing_tools=[op.value for op in understanding.requested_operations],
                warnings=[reason],
            )

        # Case 2: Needs clarification
        if understanding.needs_clarification:
            return ExecutionPlan(
                request_id=understanding.request_id,
                application=understanding.application,
                project_id=understanding.project_id,
                summary="Clarification is required before an execution plan can be finalized.",
                validation_status=PlanValidationStatus.NEEDS_CLARIFICATION,
                steps=[],
                requires_user_approval=False,
                warnings=list(understanding.clarification_questions),
            )

        # Case 3: Pure conceptual explanation (no tool execution needed)
        if understanding.requested_operations == [OperationCategory.EXPLAIN_CONCEPT]:
            step = PlanStep(
                step_number=1,
                title="Explain Engineering Concept",
                description=understanding.intent_summary,
                operation=OperationCategory.EXPLAIN_CONCEPT,
                tool_name=None,
                arguments={},
                tool_available=True,
                modifies_files=False,
                requires_approval=False,
                approval_status=ApprovalStatus.NOT_REQUIRED,
                status=StepExecutionStatus.COMPLETED,
            )
            return ExecutionPlan(
                request_id=understanding.request_id,
                application=understanding.application,
                project_id=understanding.project_id,
                summary="Provide engineering explanation without modifying or running external tools.",
                validation_status=PlanValidationStatus.VALID,
                steps=[step],
                requires_user_approval=False,
            )

        # Case 4: Build tool execution steps
        steps: List[PlanStep] = []
        missing_tools: List[str] = []
        warnings: List[str] = []
        requires_approval = False

        for idx, op in enumerate(understanding.requested_operations, start=1):
            candidates = self.registry.find_tools_by_category(
                category=op,
                application=understanding.application,
            )
            if not candidates:
                missing_tools.append(op.value)
                warnings.append(
                    f"No approved tool in ToolRegistry supports operation '{op.value}' for application '{understanding.application.value}'."
                )
                continue

            tool: ToolDefinition = candidates[0]
            raw_args = self._build_step_arguments(tool, understanding, ctx)

            # Validate arguments against ToolRegistry schema (without requiring availability yet so we can report unavailable cleanly)
            try:
                validated_call = self.registry.validate_call(
                    tool.name,
                    raw_args,
                    require_available=False,
                )
                step_args = validated_call.arguments
                validation_err: Optional[str] = None
            except ToolValidationError as exc:
                step_args = raw_args
                validation_err = str(exc)

            is_avail = tool.is_available
            if not is_avail:
                missing_tools.append(tool.name)
                warnings.append(
                    f"Required tool '{tool.name}' is registered but currently unavailable on the host."
                )

            step_requires_approval = tool.modifies_files or tool.requires_approval
            if step_requires_approval:
                requires_approval = True
                approval_state = ApprovalStatus.PENDING_APPROVAL
                step_status = StepExecutionStatus.AWAITING_APPROVAL
                warnings.append(
                    f"Step {idx} ('{tool.name}') modifies engineering files and requires explicit user approval before execution."
                )
            elif not is_avail:
                approval_state = ApprovalStatus.NOT_REQUIRED
                step_status = StepExecutionStatus.BLOCKED_UNAVAILABLE_TOOL
            elif validation_err:
                approval_state = ApprovalStatus.NOT_REQUIRED
                step_status = StepExecutionStatus.FAILED
                warnings.append(validation_err)
            else:
                approval_state = ApprovalStatus.NOT_REQUIRED
                step_status = StepExecutionStatus.PENDING

            depends_on = [idx - 1] if idx > 1 else []
            steps.append(
                PlanStep(
                    step_number=idx,
                    title=self._step_title(tool),
                    description=tool.description,
                    operation=op,
                    tool_name=tool.name,
                    arguments=step_args,
                    tool_available=is_avail,
                    modifies_files=tool.modifies_files,
                    requires_approval=step_requires_approval,
                    approval_status=approval_state,
                    status=step_status,
                    depends_on=depends_on,
                    error_message=validation_err,
                )
            )

        # Determine overall validation_status
        if not steps and missing_tools:
            validation_status = PlanValidationStatus.UNSUPPORTED_OPERATION
        elif any(not s.tool_available for s in steps):
            validation_status = PlanValidationStatus.TOOL_UNAVAILABLE
        elif any(s.error_message for s in steps):
            validation_status = PlanValidationStatus.NEEDS_CLARIFICATION
        elif requires_approval:
            validation_status = PlanValidationStatus.REQUIRES_APPROVAL
        else:
            validation_status = PlanValidationStatus.VALID

        summary = (
            f"Generated {len(steps)}-step execution plan for {understanding.application.value.upper()} "
            f"({understanding.intent_summary})"
        )

        return ExecutionPlan(
            request_id=understanding.request_id,
            application=understanding.application,
            project_id=understanding.project_id,
            summary=summary,
            validation_status=validation_status,
            steps=steps,
            requires_user_approval=requires_approval,
            missing_tools=missing_tools,
            warnings=warnings,
        )

    def _step_title(self, tool: ToolDefinition) -> str:
        titles = {
            "kicad_inspect_project": "Inspect KiCad Project Structure",
            "kicad_run_drc": "Run KiCad PCB Design Rule Check (DRC)",
            "kicad_run_erc": "Run KiCad Schematic Electrical Rules Check (ERC)",
            "kicad_export_bom": "Export KiCad Bill of Materials (BOM)",
            "kicad_export_netlist": "Export KiCad Schematic Connectivity Netlist",
            "kicad_export_gerbers": "Export KiCad Gerber & Drill Fabrication Files",
            "kicad_update_netclass_design_rules": "Update KiCad Netclass Design Rules (Requires Approval)",
            "kicad_update_schematic_component": "Update KiCad Schematic Component Value/Footprint (Requires Approval)",
            "rtl_inspect_project": "Inspect RTL/Verilog Project Hierarchy",
            "rtl_run_lint": "Run Verilator Static RTL Lint Check",
            "rtl_run_simulation": "Compile and Run RTL Simulation Testbench",
            "rtl_generate_module": "Generate New Synthesizable RTL Module (Requires Approval)",
            "rtl_apply_patch": "Apply RTL Source Code Modification (Requires Approval)",
        }
        return titles.get(tool.name, f"Execute {tool.name}")

    def _build_step_arguments(
        self,
        tool: ToolDefinition,
        understanding: RequestUnderstanding,
        ctx: ProjectContext,
    ) -> Dict[str, Any]:
        """Map extracted parameters and project files to the tool's expected schema."""
        args: Dict[str, Any] = {}
        params = understanding.extracted_parameters
        files = understanding.target_files or ctx.files

        if understanding.project_id:
            args["project_id"] = understanding.project_id

        board_files = [f for f in files if f.lower().endswith(".kicad_pcb")]
        sch_files = [f for f in files if f.lower().endswith(".kicad_sch")]
        rtl_files = [f for f in files if f.lower().endswith((".v", ".sv"))]

        param_names = {p.name for p in tool.parameters}

        if "board_file" in param_names:
            if "board_file" in params:
                args["board_file"] = params["board_file"]
            elif board_files:
                args["board_file"] = board_files[0]

        if "schematic_file" in param_names:
            if "schematic_file" in params:
                args["schematic_file"] = params["schematic_file"]
            elif sch_files:
                args["schematic_file"] = sch_files[0]

        if "source_files" in param_names:
            if "source_files" in params and isinstance(params["source_files"], list):
                args["source_files"] = params["source_files"]
            elif rtl_files:
                args["source_files"] = rtl_files

        if "target_file" in param_names:
            if "target_file" in params:
                args["target_file"] = params["target_file"]
            elif rtl_files:
                args["target_file"] = rtl_files[0]

        # Copy matching optional/extracted parameters
        for p in tool.parameters:
            if p.name in params and p.name not in args:
                args[p.name] = params[p.name]

        return args
