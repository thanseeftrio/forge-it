"""Module 5: Result Analyzer for the FORGE IT AI Agent Engine.

Responsibilities:
- Analyse actual engineering tool output (KiCad DRC/ERC/BOM/Gerber and Verilator RTL lint/simulation).
- Explain errors and warnings in understandable language.
- Identify affected files, line numbers, components, and nets when available.
- Suggest possible corrections (`CorrectedSnippet` with `is_ai_suggestion=True`).
- Strictly distinguish verified facts (`verified_facts` & `findings`) from AI-generated suggestions (`ai_suggestions`).
- Never fabricate successful execution results when a tool was not executed, timed out, or failed.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .prompts import (
    RESULT_ANALYSIS_SYSTEM_PROMPT,
    build_result_analysis_user_prompt,
)
from .provider import GeminiProvider, LLMProvider
from .schemas import (
    BackendExecutionResponse,
    CorrectedSnippet,
    EngineeringFinding,
    ExecutionPlan,
    IssueSeverity,
    ResultAnalysis,
)

# Regex for Verilator lint/compile output:
# e.g. %Error: alu.v:42:10: Syntax error
# e.g. %Warning-WIDTH: core/alu.v:27:8: Operator ASSIGN expects 8 bits on the Assign RHS, but Assign RHS's VARREF 'in_b' generates 4 bits.
_VERILATOR_DIAG_PATTERN = re.compile(
    r"^%(Error|Warning)(?:-([A-Z0-9_]+))?:\s*([^:\n]+):(\d+):(?:(\d+):)?\s*(.+)$",
    re.MULTILINE,
)

# Regex for KiCad CLI text DRC/ERC lines if emitted in stdout/stderr:
# e.g. [clearance]: Clearance violation (netclass 'Default' clearance 0.2000 mm; actual 0.1200 mm) (Severity: error) @(110.5 mm, 85.2 mm): Track [GND] on F.Cu
_KICAD_TEXT_VIOLATION_PATTERN = re.compile(
    r"^\[([a-zA-Z0-9_]+)\]:\s*(.+?)(?:\((?:Severity:\s*)?(error|warning)\))?$",
    re.MULTILINE | re.IGNORECASE,
)

# Extract component designators (R1, C12, U3, J1) or net names in brackets [GND]
_COMPONENT_REF_PATTERN = re.compile(r"\b([RCULJDQSWTP][0-9]{1,4})\b")
_BRACKET_NET_PATTERN = re.compile(r"\[([A-Za-z0-9_+\-/]+)\]")


class ResultAnalyzer:
    """Module 5: Analyzes actual tool execution outputs and generates verified facts + AI suggestions."""

    def __init__(self, provider: Optional[LLMProvider] = None) -> None:
        self.provider: LLMProvider = provider or GeminiProvider()

    def analyze(
        self,
        *,
        query: str,
        plan: ExecutionPlan,
        execution_responses: List[BackendExecutionResponse],
    ) -> ResultAnalysis:
        """Analyze actual backend execution responses and produce a `ResultAnalysis`.

        Guarantees:
        - If `execution_responses` is empty, `tool_executed` is `False` and `execution_succeeded` is `False`.
        - If any tool timed out, was unavailable, or failed to run, `execution_succeeded` is `False`.
        - `verified_facts` and `findings` are deterministically anchored to real tool output.
        - `ai_suggestions` are explicitly marked with `is_ai_suggestion=True`.
        """
        task_ids = [r.task_id for r in execution_responses if r.task_id]

        # 1. Guard against non-executed plans (never fabricate results)
        if not execution_responses:
            return ResultAnalysis(
                plan_id=plan.plan_id,
                task_ids=[],
                tool_executed=False,
                execution_succeeded=False,
                status_summary="No engineering tool was executed by the backend.",
                error_count=0,
                warning_count=0,
                verified_facts=[
                    "No tool execution occurred (plan is either awaiting user approval, needs clarification, or was not dispatched)."
                ],
                findings=[],
                plain_english_explanation=(
                    "No tool output is available to analyze because no backend tool task has been run yet."
                ),
                ai_suggestions=[],
                next_steps=self._default_next_steps_for_unexecuted_plan(plan),
            )

        # 2. Check if all responses failed to execute (unavailable / timeout / transport error)
        any_ran = any(r.status == "completed" for r in execution_responses)
        all_succeeded = all(r.status == "completed" for r in execution_responses)

        # 3. Deterministically extract verified findings and facts from the actual outputs
        findings, verified_facts, error_count, warning_count = (
            self._extract_verified_findings_and_facts(execution_responses)
        )

        if not any_ran:
            failure_reasons = [
                f"Task '{r.task_id}' ({r.tool_name}) ended with status '{r.status}': {r.error or r.stderr or 'No output'}"
                for r in execution_responses
            ]
            return ResultAnalysis(
                plan_id=plan.plan_id,
                task_ids=task_ids,
                tool_executed=False,
                execution_succeeded=False,
                status_summary=(
                    f"Tool execution did not complete ({execution_responses[0].status}). No results were fabricated."
                ),
                error_count=error_count,
                warning_count=warning_count,
                verified_facts=failure_reasons,
                findings=findings,
                plain_english_explanation=(
                    "The backend could not complete the requested engineering tool execution. "
                    "Please check that the local EDA tool (kicad-cli or verilator) and backend service are available."
                ),
                ai_suggestions=[],
                next_steps=[
                    "Verify tool installation via GET /api/integrations/status.",
                    "Retry the operation once the backend adapter is reachable.",
                ],
            )

        # 4. Build baseline deterministic explanation and AI correction suggestions
        deterministic_explanation = self._build_plain_english_explanation(
            execution_responses, findings, error_count, warning_count
        )
        deterministic_suggestions = self._build_ai_suggestions(findings, execution_responses)
        deterministic_next_steps = self._build_next_steps(
            error_count, warning_count, findings
        )
        headline = self._build_headline_summary(
            execution_responses, all_succeeded, error_count, warning_count
        )

        # 5. Optionally enrich explanation & AI suggestions via Gemini while locking verified facts
        if self.provider.is_live_api_configured:
            raw_payloads = [r.model_dump(mode="json") for r in execution_responses]
            pre_findings = [f.model_dump(mode="json") for f in findings]
            prompt = build_result_analysis_user_prompt(
                query=query,
                plan_summary=plan.summary,
                raw_execution_payloads=raw_payloads,
                pre_extracted_findings=pre_findings,
            )
            llm_analysis = self.provider.generate_structured(
                system_instruction=RESULT_ANALYSIS_SYSTEM_PROMPT,
                user_prompt=prompt,
                response_model=ResultAnalysis,
                temperature=0.1,
            )
            if llm_analysis is not None:
                # Enforce ground-truth invariants over LLM output so it can never fabricate success or drop verified findings
                merged_facts = list(verified_facts)
                for fact in llm_analysis.verified_facts:
                    if fact not in merged_facts:
                        merged_facts.append(fact)

                merged_suggestions = (
                    llm_analysis.ai_suggestions
                    if llm_analysis.ai_suggestions
                    else deterministic_suggestions
                )
                return ResultAnalysis(
                    plan_id=plan.plan_id,
                    task_ids=task_ids,
                    tool_executed=True,
                    execution_succeeded=all_succeeded,
                    status_summary=headline,
                    error_count=error_count,
                    warning_count=warning_count,
                    verified_facts=merged_facts,
                    findings=findings if findings else llm_analysis.findings,
                    plain_english_explanation=(
                        llm_analysis.plain_english_explanation or deterministic_explanation
                    ),
                    ai_suggestions=merged_suggestions,
                    next_steps=llm_analysis.next_steps or deterministic_next_steps,
                )

        return ResultAnalysis(
            plan_id=plan.plan_id,
            task_ids=task_ids,
            tool_executed=True,
            execution_succeeded=all_succeeded,
            status_summary=headline,
            error_count=error_count,
            warning_count=warning_count,
            verified_facts=verified_facts,
            findings=findings,
            plain_english_explanation=deterministic_explanation,
            ai_suggestions=deterministic_suggestions,
            next_steps=deterministic_next_steps,
        )

    def _extract_verified_findings_and_facts(
        self,
        responses: List[BackendExecutionResponse],
    ) -> Tuple[List[EngineeringFinding], List[str], int, int]:
        """Parse structured_output, stdout, and stderr from actual tool runs into verified findings."""
        findings: List[EngineeringFinding] = []
        facts: List[str] = []

        for resp in responses:
            facts.append(
                f"Tool '{resp.tool_name}' (task_id={resp.task_id}) finished with status='{resp.status}'"
                + (f", exit_code={resp.exit_code}." if resp.exit_code is not None else ".")
            )

            struct = resp.structured_output or {}

            # 1. KiCad DRC / ERC structured violations list (`violations` or `errors` / `warnings` keys)
            raw_violations = (
                struct.get("violations")
                or struct.get("drc_violations")
                or struct.get("erc_violations")
                or struct.get("issues")
                or []
            )
            default_file = (
                struct.get("board_file")
                or struct.get("schematic_file")
                or struct.get("file_path")
                or struct.get("source_file")
            )

            if isinstance(raw_violations, list):
                for item in raw_violations:
                    if not isinstance(item, dict):
                        continue
                    sev_raw = str(item.get("severity", "error")).lower()
                    if "warn" in sev_raw:
                        sev = IssueSeverity.WARNING
                    elif "info" in sev_raw:
                        sev = IssueSeverity.INFO
                    else:
                        sev = IssueSeverity.ERROR

                    code = (
                        item.get("type")
                        or item.get("code")
                        or item.get("rule")
                        or "violation"
                    )
                    msg = (
                        item.get("description")
                        or item.get("message")
                        or f"{code} violation"
                    )
                    f_path = item.get("file") or item.get("file_path") or default_file
                    line_no = item.get("line") or item.get("line_number")
                    col_no = item.get("column") or item.get("column_number")

                    comps_nets: List[str] = []
                    for key in ("components", "nets", "items", "symbols", "signals"):
                        val = item.get(key)
                        if isinstance(val, list):
                            for elem in val:
                                if isinstance(elem, str) and elem not in comps_nets:
                                    comps_nets.append(elem)
                                elif isinstance(elem, dict):
                                    desc = elem.get("description") or elem.get("ref") or elem.get("net")
                                    if desc and str(desc) not in comps_nets:
                                        comps_nets.append(str(desc))

                    # Also extract component designators/nets mentioned in msg
                    for comp in _COMPONENT_REF_PATTERN.findall(msg):
                        if comp not in comps_nets:
                            comps_nets.append(comp)
                    for net in _BRACKET_NET_PATTERN.findall(msg):
                        if net not in comps_nets:
                            comps_nets.append(net)

                    findings.append(
                        EngineeringFinding(
                            severity=sev,
                            code=str(code),
                            message=str(msg),
                            file_path=str(f_path) if f_path else None,
                            line_number=int(line_no) if isinstance(line_no, int) else None,
                            column_number=int(col_no) if isinstance(col_no, int) else None,
                            components_or_nets=comps_nets,
                            verified_from_tool=True,
                        )
                    )

            # 2. Parse Verilator lint/simulation diagnostics from stdout/stderr
            combined_log = "\n".join(
                part for part in (resp.stdout, resp.stderr) if part
            )
            for match in _VERILATOR_DIAG_PATTERN.finditer(combined_log):
                kind, rule_code, f_path, line_str, col_str, msg_body = match.groups()
                sev = (
                    IssueSeverity.ERROR
                    if kind.lower() == "error"
                    else IssueSeverity.WARNING
                )
                line_num = int(line_str) if line_str else None
                col_num = int(col_str) if col_str else None
                code_str = rule_code or kind.upper()

                # Extract signal/variable names in single quotes
                quoted_Adjustment = re.findall(r"'([A-Za-z0-9_.$]+)'", msg_body)
                findings.append(
                    EngineeringFinding(
                        severity=sev,
                        code=code_str,
                        message=msg_body.strip(),
                        file_path=f_path.strip(),
                        line_number=line_num,
                        column_number=col_num,
                        components_or_nets=quoted_Adjustment,
                        verified_from_tool=True,
                    )
                )

            # 3. Parse KiCad text-format DRC/ERC lines if no structured list was provided
            if not raw_violations:
                for match in _KICAD_TEXT_VIOLATION_PATTERN.finditer(combined_log):
                    rule_code, msg_body, sev_str = match.groups()
                    sev = (
                        IssueSeverity.WARNING
                        if (sev_str and "warn" in sev_str.lower())
                        else IssueSeverity.ERROR
                    )
                    comps_nets = list(
                        dict.fromkeys(
                            _COMPONENT_REF_PATTERN.findall(msg_body)
                            + _BRACKET_NET_PATTERN.findall(msg_body)
                        )
                    )
                    findings.append(
                        EngineeringFinding(
                            severity=sev,
                            code=rule_code,
                            message=msg_body.strip(),
                            file_path=str(default_file) if default_file else None,
                            components_or_nets=comps_nets,
                            verified_from_tool=True,
                        )
                    )

            # 4. Record BOM / project inspection metadata facts if present
            if "component_count" in struct:
                facts.append(f"Verified component count: {struct['component_count']}.")
            if "net_count" in struct:
                facts.append(f"Verified net count: {struct['net_count']}.")
            if "bom_file" in struct:
                facts.append(f"Generated BOM artifact: {struct['bom_file']}.")
            if "gerber_files" in struct and isinstance(struct["gerber_files"], list):
                facts.append(
                    f"Generated {len(struct['gerber_files'])} Gerber/Drill fabrication file(s)."
                )
            if "modules" in struct and isinstance(struct["modules"], list):
                facts.append(
                    f"Discovered RTL modules: {', '.join(str(m) for m in struct['modules'])}."
                )

        error_count = sum(1 for f in findings if f.severity == IssueSeverity.ERROR)
        warning_count = sum(1 for f in findings if f.severity == IssueSeverity.WARNING)

        facts.append(
            f"Total verified findings from tool output: {error_count} error(s), {warning_count} warning(s)."
        )
        for idx, f in enumerate(findings[:10], start=1):
            loc = ""
            if f.file_path:
                loc = f" in {f.file_path}" + (f":{f.line_number}" if f.line_number else "")
            facts.append(
                f"Finding #{idx} [{f.severity.value.upper()}] ({f.code or 'issue'}{loc}): {f.message}"
            )

        return findings, facts, error_count, warning_count

    def _build_headline_summary(
        self,
        responses: List[BackendExecutionResponse],
        all_succeeded: bool,
        error_count: int,
        warning_count: int,
    ) -> str:
        tool_names = ", ".join(r.tool_name for r in responses)
        if not all_succeeded:
            return (
                f"Execution of [{tool_names}] encountered errors: "
                f"{error_count} error(s) and {warning_count} warning(s) reported."
            )
        if error_count == 0 and warning_count == 0:
            return f"Execution of [{tool_names}] completed cleanly with 0 errors and 0 warnings."
        return (
            f"Execution of [{tool_names}] completed and found "
            f"{error_count} error(s) and {warning_count} warning(s)."
        )

    def _build_plain_english_explanation(
        self,
        responses: List[BackendExecutionResponse],
        findings: List[EngineeringFinding],
        error_count: int,
        warning_count: int,
    ) -> str:
        if not findings:
            summaries: List[str] = []
            for r in responses:
                if r.status == "completed":
                    summaries.append(
                        f"The tool `{r.tool_name}` completed with no rule violations or syntax errors reported."
                    )
                else:
                    summaries.append(
                        f"The tool `{r.tool_name}` finished with status `{r.status}` ({r.error or 'check logs'})."
                    )
            return " ".join(summaries)

        lines: List[str] = [
            f"The engineering tool output reported {error_count} error(s) and {warning_count} warning(s):"
        ]
        for f in findings[:8]:
            code_lower = (f.code or "").lower()
            loc_str = f" in `{f.file_path}`" + (f" (line {f.line_number})" if f.line_number else "") if f.file_path else ""
            targets = f" affecting {', '.join(f.components_or_nets)}" if f.components_or_nets else ""

            if "clearance" in code_lower:
                meaning = (
                    "Two copper features (tracks, pads, or zones) are closer than the minimum allowed manufacturing spacing, "
                    "which risks a solder bridge or short circuit during PCB fabrication."
                )
            elif "unconnected" in code_lower or "open" in code_lower:
                meaning = (
                    "A required net connection between pins/pads is incomplete on the board or schematic, "
                    "meaning the circuit signal or power rail is broken."
                )
            elif "track_width" in code_lower or "width" in code_lower and f.file_path and f.file_path.endswith(".kicad_pcb"):
                meaning = (
                    "A routed copper trace is narrower than the netclass or board minimum track width rule."
                )
            elif "courtyard" in code_lower:
                meaning = (
                    "Component placement courtyards overlap, which can cause mechanical collision during pick-and-place assembly."
                )
            elif "power_pin" in code_lower or "pin_not_driven" in code_lower or "erc" in code_lower:
                meaning = (
                    "An electrical rule check flagged a schematic pin conflict or undriven power/input pin."
                )
            elif "width" in code_lower:
                meaning = (
                    "An RTL bit-width mismatch was detected between the left-hand side and right-hand side of an assignment or port connection, "
                    "which can silently truncate or zero-extend MSB bits."
                )
            elif "latch" in code_lower:
                meaning = (
                    "A combinational `always` block does not assign a signal on all control paths, causing synthesis to infer an unintended level-sensitive latch."
                )
            elif "unoptflat" in code_lower or "comb" in code_lower:
                meaning = (
                    "A combinational loop or unoptimizable flat net was detected in the RTL design."
                )
            elif "undriven" in code_lower or "unused" in code_lower:
                meaning = (
                    "An RTL wire or register is declared but either never driven (floating) or never read."
                )
            else:
                meaning = f.message

            lines.append(
                f"- **[{f.severity.value.upper()}] `{f.code or 'issue'}`**{loc_str}{targets}: {f.message} — *{meaning}*"
            )

        return "\n".join(lines)

    def _build_ai_suggestions(
        self,
        findings: List[EngineeringFinding],
        responses: List[BackendExecutionResponse],
    ) -> List[CorrectedSnippet]:
        """Generate clearly labeled AI suggestions (`is_ai_suggestion=True`) for each finding."""
        suggestions: List[CorrectedSnippet] = []

        for f in findings[:8]:
            code_lower = (f.code or "").lower()
            loc_range = f"L{f.line_number}" if f.line_number else (", ".join(f.components_or_nets) or None)

            if "clearance" in code_lower:
                suggestions.append(
                    CorrectedSnippet(
                        target_file=f.file_path,
                        line_range=loc_range,
                        explanation=(
                            "[AI Suggestion] Reroute the offending trace in KiCad PCB Editor to increase spacing, "
                            "or if your PCB manufacturer supports tighter tolerances, update the netclass clearance rule."
                        ),
                        suggested_change=(
                            "In KiCad PCB Editor: Move track away from adjacent pad/zone to meet >= 0.20 mm clearance, "
                            "or request `kicad_update_netclass_design_rules` with user approval."
                        ),
                        is_ai_suggestion=True,
                    )
                )
            elif "unconnected" in code_lower:
                suggestions.append(
                    CorrectedSnippet(
                        target_file=f.file_path,
                        line_range=loc_range,
                        explanation=(
                            "[AI Suggestion] Complete the copper route between the unconnected pads on the ratsnest net, "
                            "or refill copper zones (`B` shortcut in KiCad) if the pad connects to a ground/power pour."
                        ),
                        suggested_change="Route missing track segment or press 'B' in KiCad to refill copper zones.",
                        is_ai_suggestion=True,
                    )
                )
            elif "courtyard" in code_lower:
                suggestions.append(
                    CorrectedSnippet(
                        target_file=f.file_path,
                        line_range=loc_range,
                        explanation=(
                            "[AI Suggestion] Separate the overlapping footprints on the PCB so their `F.CrtYd` / `B.CrtYd` outlines do not intersect."
                        ),
                        suggested_change="Move component footprint in KiCad PCB Editor outside adjacent courtyard boundary.",
                        is_ai_suggestion=True,
                    )
                )
            elif "power_pin" in code_lower or "pin_not_driven" in code_lower:
                suggestions.append(
                    CorrectedSnippet(
                        target_file=f.file_path,
                        line_range=loc_range,
                        explanation=(
                            "[AI Suggestion] Add a `PWR_FLAG` symbol to the power rail in the KiCad schematic if it is fed through a passive connector, "
                            "or verify that a regulator output drives the rail."
                        ),
                        suggested_change="Place `PWR_FLAG` symbol on VCC/GND net in `.kicad_sch`.",
                        is_ai_suggestion=True,
                    )
                )
            elif "width" in code_lower and f.file_path and f.file_path.endswith((".v", ".sv")):
                suggestions.append(
                    CorrectedSnippet(
                        target_file=f.file_path,
                        line_range=loc_range,
                        explanation=(
                            "[AI Suggestion] Explicitly match bit widths using concatenation or bit-slicing so synthesis and Verilator do not implicitly truncate or pad bits."
                        ),
                        suggested_change="// Example width-matched assignment:\nassign out_bus = {{4{1'b0}}, in_nibble};",
                        is_ai_suggestion=True,
                    )
                )
            elif "latch" in code_lower:
                suggestions.append(
                    CorrectedSnippet(
                        target_file=f.file_path,
                        line_range=loc_range,
                        explanation=(
                            "[AI Suggestion] Initialize all outputs at the top of the `always_comb` / `always @(*)` block or ensure every `if`/`case` branch assigns the signal."
                        ),
                        suggested_change="always @(*) begin\n    next_state = state; // Default assignment prevents latch\n    // ... case/if logic ...\nend",
                        is_ai_suggestion=True,
                    )
                )
            else:
                suggestions.append(
                    CorrectedSnippet(
                        target_file=f.file_path,
                        line_range=loc_range,
                        explanation=(
                            f"[AI Suggestion] Inspect `{f.code or 'issue'}` ({f.message}) and update the design constraint or source connection accordingly."
                        ),
                        suggested_change=None,
                        is_ai_suggestion=True,
                    )
                )

        return suggestions

    def _build_next_steps(
        self,
        error_count: int,
        warning_count: int,
        findings: List[EngineeringFinding],
    ) -> List[str]:
        if error_count == 0 and warning_count == 0:
            return [
                "Review the verified output summary.",
                "Proceed to the next stage of your workflow (e.g., BOM/Gerber export or RTL testbench simulation).",
            ]
        steps = [
            f"Resolve the {error_count} verified error(s) first, starting with the affected files/components listed above.",
        ]
        if warning_count > 0:
            steps.append(
                f"Review the {warning_count} warning(s) to confirm whether they are acceptable or require design updates."
            )
        steps.append(
            "Re-run the check through FORGE IT after making changes to verify a clean report."
        )
        return steps

    def _default_next_steps_for_unexecuted_plan(self, plan: ExecutionPlan) -> List[str]:
        if plan.requires_user_approval:
            return [
                "Review the proposed file-modifying step(s) in the execution plan.",
                "Approve or reject the modification to continue.",
            ]
        if plan.missing_tools:
            return [
                f"Unavailable or unsupported tool(s): {', '.join(plan.missing_tools)}.",
                "Ensure required EDA tools are installed or adjust the request to use approved tools.",
            ]
        return ["Provide the requested clarification details to proceed."]
