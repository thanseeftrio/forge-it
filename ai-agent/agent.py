"""Top-level FORGE IT AI Agent Orchestrator (`ForgeAgent`).

Combines:
1. `RequestUnderstandingEngine` (Module 1)
2. `PlanningEngine` (Module 2)
3. `ToolRegistry` (Module 3)
4. `ExecutionCoordinator` (Module 4)
5. `ResultAnalyzer` (Module 5)

Provides a clean, FastAPI-ready interface for Member 1's backend and Member 2's frontend:
- `handle_turn(request: AgentTurnRequest) -> AgentTurnResponse`
- `understand_request(query, project_context) -> RequestUnderstanding`
- `generate_plan(understanding, project_context) -> ExecutionPlan`
- `approve_and_execute(decision: ApprovalDecisionRequest) -> AgentTurnResponse`
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .coordinator import BackendClientProtocol, ExecutionCoordinator
from .planner import PlanningEngine, RequestUnderstandingEngine
from .provider import GeminiProvider, LLMProvider
from .result_analyzer import ResultAnalyzer
from .schemas import (
    AgentTurnRequest,
    AgentTurnResponse,
    ApplicationType,
    ApprovalDecisionRequest,
    ExecutionPlan,
    OperationCategory,
    PlanValidationStatus,
    ProjectContext,
    RequestUnderstanding,
    ResultAnalysis,
)
from .tool_registry import ToolRegistry, get_default_tool_registry


class ForgeAgent:
    """Main entry point for the FORGE IT AI Agent Engine."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        provider: Optional[LLMProvider] = None,
        backend_client: Optional[BackendClientProtocol] = None,
    ) -> None:
        self.registry: ToolRegistry = registry or get_default_tool_registry()
        self.provider: LLMProvider = provider or GeminiProvider()
        self.understanding_engine = RequestUnderstandingEngine(
            registry=self.registry,
            provider=self.provider,
        )
        self.planning_engine = PlanningEngine(registry=self.registry)
        self.coordinator = ExecutionCoordinator(
            registry=self.registry,
            backend_client=backend_client,
        )
        self.analyzer = ResultAnalyzer(provider=self.provider)

        # In-memory store of generated plans awaiting user approval or inspection
        self._plans: Dict[str, ExecutionPlan] = {}
        self._understandings: Dict[str, RequestUnderstanding] = {}

    def get_plan(self, plan_id: str) -> Optional[ExecutionPlan]:
        """Retrieve a stored ExecutionPlan by ID."""
        return self._plans.get(plan_id)

    def understand_request(
        self,
        query: str,
        project_context: Optional[ProjectContext] = None,
    ) -> RequestUnderstanding:
        """Module 1: Parse a natural-language engineering request into structured JSON."""
        understanding = self.understanding_engine.understand(
            query=query,
            project_context=project_context,
        )
        self._understandings[understanding.request_id] = understanding
        return understanding

    def generate_plan(
        self,
        understanding: RequestUnderstanding,
        project_context: Optional[ProjectContext] = None,
    ) -> ExecutionPlan:
        """Module 2: Generate a validated step-by-step ExecutionPlan from a RequestUnderstanding."""
        plan = self.planning_engine.create_plan(
            understanding=understanding,
            project_context=project_context,
        )
        self._plans[plan.plan_id] = plan
        return plan

    def handle_turn(self, request: AgentTurnRequest) -> AgentTurnResponse:
        """End-to-end orchestration of an engineering request turn.

        Workflow:
        1. Understand the request (Module 1).
        2. If clarification is needed, return immediately with `status="needs_clarification"`.
        3. Generate a step-by-step plan validated against `ToolRegistry` (Module 2 & 3).
        4. If the operation is unsupported or required tools are unavailable, return without executing.
        5. If the plan modifies files and `user_approved_modifications` is False, pause and return
           `status="awaiting_approval"` so the user can review and approve.
        6. Otherwise, execute the plan through `ExecutionCoordinator` (Module 4) and analyze
           the actual tool results via `ResultAnalyzer` (Module 5).
        """
        understanding = self.understand_request(
            query=request.query,
            project_context=request.project_context,
        )

        # 1. Clarification required
        if understanding.needs_clarification:
            plan = self.generate_plan(understanding, request.project_context)
            questions_text = "\n".join(
                f"- {q}" for q in understanding.clarification_questions
            )
            return AgentTurnResponse(
                status="needs_clarification",
                understanding=understanding,
                plan=plan,
                execution_results=[],
                analysis=None,
                message=(
                    f"I need a bit more information before running this engineering task:\n{questions_text}"
                ),
            )

        # 2. Build execution plan
        plan = self.generate_plan(understanding, request.project_context)

        # 3. Unsupported operation
        if plan.validation_status == PlanValidationStatus.UNSUPPORTED_OPERATION:
            reason = (
                understanding.unsupported_reason
                or (plan.warnings[0] if plan.warnings else "Operation is not supported by approved tools.")
            )
            return AgentTurnResponse(
                status="unsupported",
                understanding=understanding,
                plan=plan,
                execution_results=[],
                analysis=None,
                message=f"Unsupported operation: {reason}",
            )

        # 4. Required tool is unavailable on host
        if plan.validation_status == PlanValidationStatus.TOOL_UNAVAILABLE:
            missing_str = ", ".join(plan.missing_tools)
            return AgentTurnResponse(
                status="tool_unavailable",
                understanding=understanding,
                plan=plan,
                execution_results=[],
                analysis=None,
                message=(
                    f"Cannot execute plan because required engineering tool(s) [{missing_str}] "
                    "are currently unavailable on the backend host."
                ),
            )

        # 5. Pure concept explanation (no external tool call needed)
        if understanding.requested_operations == [OperationCategory.EXPLAIN_CONCEPT]:
            return AgentTurnResponse(
                status="completed",
                understanding=understanding,
                plan=plan,
                execution_results=[],
                analysis=None,
                message=(
                    f"Concept explanation request understood ({understanding.intent_summary}). "
                    "No external EDA tool execution was required."
                ),
            )

        # 6. Respect user approval before modifying engineering files
        if plan.requires_user_approval and not request.user_approved_modifications:
            return AgentTurnResponse(
                status="awaiting_approval",
                understanding=understanding,
                plan=plan,
                execution_results=[],
                analysis=None,
                message=(
                    f"Plan `{plan.plan_id}` includes file-modifying operation(s) and requires your explicit "
                    "approval before execution. Please review the plan steps and approve to proceed."
                ),
            )

        # 7. If caller requested plan-only (`auto_execute_read_only=False` and not explicitly approved)
        if not request.auto_execute_read_only and not request.user_approved_modifications:
            return AgentTurnResponse(
                status="plan_ready",
                understanding=understanding,
                plan=plan,
                execution_results=[],
                analysis=None,
                message=f"Execution plan `{plan.plan_id}` is ready with {len(plan.steps)} step(s).",
            )

        # 8. Execute via Backend Coordinator and Analyze Actual Results
        updated_plan, exec_responses = self.coordinator.execute_plan(
            plan=plan,
            user_approved_modifications=request.user_approved_modifications,
        )
        self._plans[updated_plan.plan_id] = updated_plan

        analysis = self.analyzer.analyze(
            query=request.query,
            plan=updated_plan,
            execution_responses=exec_responses,
        )

        overall_status = (
            "completed" if analysis.execution_succeeded else "execution_failed"
        )
        user_message = self._compose_user_message(analysis)

        return AgentTurnResponse(
            status=overall_status,
            understanding=understanding,
            plan=updated_plan,
            execution_results=exec_responses,
            analysis=analysis,
            message=user_message,
        )

    def approve_and_execute(
        self,
        decision: ApprovalDecisionRequest,
    ) -> AgentTurnResponse:
        """Apply a user's approval/rejection decision to a pending plan and execute if approved."""
        plan = self._plans.get(decision.plan_id)
        if plan is None:
            raise KeyError(f"Plan '{decision.plan_id}' not found.")

        understanding = self._understandings.get(plan.request_id) or RequestUnderstanding(
            request_id=plan.request_id,
            raw_query=plan.summary,
            application=plan.application,
            project_id=plan.project_id,
            intent_summary=plan.summary,
            requested_operations=[s.operation for s in plan.steps],
            modifies_files=plan.requires_user_approval,
        )

        updated_plan = self.coordinator.apply_approval_decision(plan, decision)
        self._plans[updated_plan.plan_id] = updated_plan

        if not decision.approved:
            return AgentTurnResponse(
                status="unsupported",
                understanding=understanding,
                plan=updated_plan,
                execution_results=[],
                analysis=None,
                message=f"File modification in plan `{updated_plan.plan_id}` was rejected by user. No files were changed.",
            )

        executed_plan, exec_responses = self.coordinator.execute_plan(
            plan=updated_plan,
            user_approved_modifications=True,
        )
        self._plans[executed_plan.plan_id] = executed_plan

        analysis = self.analyzer.analyze(
            query=understanding.raw_query,
            plan=executed_plan,
            execution_responses=exec_responses,
        )
        overall_status = (
            "completed" if analysis.execution_succeeded else "execution_failed"
        )

        return AgentTurnResponse(
            status=overall_status,
            understanding=understanding,
            plan=executed_plan,
            execution_results=exec_responses,
            analysis=analysis,
            message=self._compose_user_message(analysis),
        )

    def propose_fix_plan(
        self,
        analysis: ResultAnalysis,
        project_context: ProjectContext,
    ) -> Optional[ExecutionPlan]:
        """Generate an approval-gated Fix + Re-verify ExecutionPlan from a ResultAnalysis.

        When DRC/ERC or RTL lint detects errors and AI suggestions are available:
        - Step 1: Apply the approved fix (`kicad_update_netclass_design_rules` or `rtl_apply_patch`)
                  with `requires_approval=True` (`PENDING_APPROVAL`).
        - Step 2: Re-run the verification tool (`kicad_run_drc` or `rtl_run_lint`) to confirm
                  that the fix resolved the violation.
        """
        if not analysis.findings or not analysis.ai_suggestions:
            return None

        first_finding = analysis.findings[0]
        first_suggestion = analysis.ai_suggestions[0]
        target_file = first_suggestion.target_file or first_finding.file_path

        if not target_file or not project_context.project_id:
            return None

        if target_file.lower().endswith(".kicad_pcb"):
            synthetic_understanding = RequestUnderstanding(
                raw_query=f"Fix {first_finding.code or 'DRC'} violation on {target_file} and re-run DRC",
                application=ApplicationType.KICAD,
                project_id=project_context.project_id,
                target_files=[target_file],
                intent_summary=f"Apply design rule fix on {target_file} and re-verify with KiCad DRC.",
                requested_operations=[
                    OperationCategory.MODIFY_PCB_PARAMETER,
                    OperationCategory.DRC_CHECK,
                ],
                extracted_parameters={
                    "project_id": project_context.project_id,
                    "board_file": target_file,
                    "netclass_name": "Default",
                    "clearance_mm": 0.15,
                },
                modifies_files=True,
                needs_clarification=False,
            )
            return self.generate_plan(synthetic_understanding, project_context)

        if target_file.lower().endswith((".v", ".sv")):
            patch_code = (
                first_suggestion.suggested_change
                or "// Applied AI-suggested fix for RTL lint violation"
            )
            synthetic_understanding = RequestUnderstanding(
                raw_query=f"Fix {first_finding.code or 'RTL'} issue in {target_file} and re-run Verilator lint",
                application=ApplicationType.RTL,
                project_id=project_context.project_id,
                target_files=[target_file],
                intent_summary=f"Apply RTL patch to {target_file} and re-verify with Verilator lint.",
                requested_operations=[
                    OperationCategory.MODIFY_RTL_SOURCE,
                    OperationCategory.RTL_LINT,
                ],
                extracted_parameters={
                    "project_id": project_context.project_id,
                    "target_file": target_file,
                    "source_files": [target_file],
                    "updated_content": patch_code,
                    "change_description": first_suggestion.explanation,
                },
                modifies_files=True,
                needs_clarification=False,
            )
            return self.generate_plan(synthetic_understanding, project_context)

        return None

    def _compose_user_message(self, analysis: ResultAnalysis) -> str:
        """Format a clear response that distinguishes verified facts from AI suggestions."""
        sections: List[str] = [
            f"### {analysis.status_summary}",
            "",
            "#### Verified Tool Facts",
        ]
        for fact in analysis.verified_facts:
            sections.append(f"- {fact}")

        sections.append("")
        sections.append("#### Engineering Explanation")
        sections.append(analysis.plain_english_explanation)

        if analysis.ai_suggestions:
            sections.append("")
            sections.append("#### AI-Suggested Corrections *(Unverified Suggestions — Review Before Applying)*")
            for s in analysis.ai_suggestions:
                loc = f" (`{s.target_file}` {s.line_range or ''})".rstrip() if s.target_file else ""
                sections.append(f"- {s.explanation}{loc}")
                if s.suggested_change:
                    sections.append(f"  ```\n  {s.suggested_change}\n  ```")

        return "\n".join(sections)
