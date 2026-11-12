"""Unit and Integration Tests for the FORGE IT AI Agent Engine.

Covers:
1. Request Understanding (KiCad DRC/ERC/BOM/Gerber/Modify, RTL Lint/Simulate/Patch, clarification, unsupported ops)
2. Tool Registry (approved tools, schema validation, path traversal & shell injection blocking, never inventing tools)
3. Planning Engine (step generation, tool availability checks, approval requirements for file modifications)
4. Execution Coordinator (backend communication, job polling, timeout/unavailable handling, approval enforcement)
5. Result Analyzer (KiCad DRC/ERC & Verilator lint parsing, verified facts vs AI suggestions separation, never fabricating success)
6. End-to-End `ForgeAgent` workflows (KiCad priority workflow + RTL workflow + Gemini structured output mock)
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Add repository root so we can import `ai-agent` cleanly
REPO_ROOT = Path(__file__).resolve().parents[2]
AI_AGENT_DIR = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import importlib.util

# Load `ai_agent` package dynamically from `ai-agent/` directory
spec = importlib.util.spec_from_file_location(
    "ai_agent",
    AI_AGENT_DIR / "__init__.py",
    submodule_search_locations=[str(AI_AGENT_DIR)],
)
assert spec is not None and spec.loader is not None
ai_agent = importlib.util.module_from_spec(spec)
sys.modules["ai_agent"] = ai_agent
spec.loader.exec_module(ai_agent)

from ai_agent.agent import ForgeAgent
from ai_agent.coordinator import (
    ExecutionCoordinator,
    InProcessBackendAdapter,
)
from ai_agent.planner import PlanningEngine, RequestUnderstandingEngine
from ai_agent.provider import GeminiProvider, MockGeminiProvider, _scrub_secrets, normalize_gemini_model_name
from ai_agent.result_analyzer import ResultAnalyzer
from ai_agent.schemas import (
    AgentTurnRequest,
    ApplicationType,
    ApprovalDecisionRequest,
    ApprovalStatus,
    BackendExecutionRequest,
    BackendExecutionResponse,
    BackendJobStatusResponse,
    IssueSeverity,
    OperationCategory,
    PlanValidationStatus,
    ProjectContext,
    RequestUnderstanding,
    StepExecutionStatus,
)
from ai_agent.tool_registry import ToolValidationError, get_default_tool_registry


class TestToolRegistry(unittest.TestCase):
    """Tests for Module 3: Tool Registry."""

    def setUp(self) -> None:
        self.registry = get_default_tool_registry()

    def test_approved_kicad_and_rtl_tools_registered(self) -> None:
        kicad_tools = self.registry.list_tools(application=ApplicationType.KICAD)
        rtl_tools = self.registry.list_tools(application=ApplicationType.RTL)
        self.assertGreaterEqual(len(kicad_tools), 6)
        self.assertGreaterEqual(len(rtl_tools), 4)
        self.assertTrue(self.registry.is_registered("kicad_run_drc"))
        self.assertTrue(self.registry.is_registered("kicad_run_erc"))
        self.assertTrue(self.registry.is_registered("rtl_run_lint"))

    def test_reject_invented_tool_name(self) -> None:
        with self.assertRaises(ToolValidationError):
            self.registry.validate_call("kicad_magic_autorouter", {"project_id": "p1"})

    def test_validate_kicad_drc_arguments_and_defaults(self) -> None:
        call = self.registry.validate_call(
            "kicad_run_drc",
            {"project_id": "proj_1", "board_file": "boards/power_supply.kicad_pcb"},
        )
        self.assertEqual(call.tool_name, "kicad_run_drc")
        self.assertEqual(call.arguments["severity_filter"], "all")
        self.assertTrue(call.arguments["check_unconnected"])

    def test_block_path_traversal_and_shell_injection(self) -> None:
        with self.assertRaises(ToolValidationError):
            self.registry.validate_call(
                "kicad_run_drc",
                {"project_id": "proj_1", "board_file": "../../etc/passwd"},
            )
        with self.assertRaises(ToolValidationError):
            self.registry.validate_call(
                "kicad_run_drc",
                {"project_id": "proj_1", "board_file": "board.kicad_pcb; rm -rf /"},
            )
        with self.assertRaises(ToolValidationError):
            self.registry.validate_call(
                "kicad_run_drc",
                {"project_id": "proj_1", "board_file": "/abs/path/board.kicad_pcb"},
            )

    def test_reject_unexpected_parameters(self) -> None:
        with self.assertRaises(ToolValidationError):
            self.registry.validate_call(
                "kicad_run_drc",
                {
                    "project_id": "proj_1",
                    "board_file": "board.kicad_pcb",
                    "shell_cmd": "whoami",
                },
            )


class TestRequestUnderstandingAndPlanning(unittest.TestCase):
    """Tests for Module 1 (Request Understanding) and Module 2 (Planning Engine)."""

    def setUp(self) -> None:
        self.registry = get_default_tool_registry()
        self.understanding_engine = RequestUnderstandingEngine(registry=self.registry)
        self.planner = PlanningEngine(registry=self.registry)

    def test_kicad_drc_and_erc_request_understanding_and_planning(self) -> None:
        ctx = ProjectContext(
            project_id="pcb_motor_drv",
            application=ApplicationType.KICAD,
            files=["motor_driver.kicad_pcb", "motor_driver.kicad_sch"],
        )
        u = self.understanding_engine.understand(
            "Run DRC on the PCB and ERC on the schematic for my motor driver board",
            project_context=ctx,
        )
        self.assertEqual(u.application, ApplicationType.KICAD)
        self.assertFalse(u.needs_clarification)
        self.assertIn(OperationCategory.DRC_CHECK, u.requested_operations)
        self.assertIn(OperationCategory.ERC_CHECK, u.requested_operations)

        plan = self.planner.create_plan(u, ctx)
        self.assertEqual(plan.validation_status, PlanValidationStatus.VALID)
        self.assertEqual(len(plan.steps), 2)
        self.assertEqual(plan.steps[0].tool_name, "kicad_run_drc")
        self.assertEqual(plan.steps[0].arguments["board_file"], "motor_driver.kicad_pcb")
        self.assertEqual(plan.steps[1].tool_name, "kicad_run_erc")
        self.assertEqual(plan.steps[1].arguments["schematic_file"], "motor_driver.kicad_sch")

    def test_clarification_requested_when_project_or_file_missing(self) -> None:
        u = self.understanding_engine.understand(
            "Run DRC check",
            project_context=ProjectContext(),
        )
        self.assertEqual(u.application, ApplicationType.KICAD)
        self.assertTrue(u.needs_clarification)
        self.assertGreaterEqual(len(u.clarification_questions), 1)

        plan = self.planner.create_plan(u)
        self.assertEqual(plan.validation_status, PlanValidationStatus.NEEDS_CLARIFICATION)
        self.assertEqual(len(plan.steps), 0)

    def test_unsupported_operation_rejected(self) -> None:
        ctx = ProjectContext(
            project_id="pcb_1",
            application=ApplicationType.KICAD,
            files=["main.kicad_pcb"],
        )
        u = self.understanding_engine.understand(
            "Please autoroute all traces on main.kicad_pcb automatically",
            project_context=ctx,
        )
        self.assertIn(OperationCategory.UNSUPPORTED, u.requested_operations)
        self.assertIsNotNone(u.unsupported_reason)

        plan = self.planner.create_plan(u, ctx)
        self.assertEqual(plan.validation_status, PlanValidationStatus.UNSUPPORTED_OPERATION)

    def test_unavailable_tool_detected_in_plan(self) -> None:
        self.registry.set_tool_availability("kicad_run_drc", False)
        ctx = ProjectContext(
            project_id="pcb_1",
            application=ApplicationType.KICAD,
            files=["main.kicad_pcb"],
        )
        u = self.understanding_engine.understand("Run DRC on main.kicad_pcb", ctx)
        plan = self.planner.create_plan(u, ctx)
        self.assertEqual(plan.validation_status, PlanValidationStatus.TOOL_UNAVAILABLE)
        self.assertIn("kicad_run_drc", plan.missing_tools)
        self.assertEqual(
            plan.steps[0].status, StepExecutionStatus.BLOCKED_UNAVAILABLE_TOOL
        )

    def test_file_modification_requires_user_approval(self) -> None:
        ctx = ProjectContext(
            project_id="pcb_1",
            application=ApplicationType.KICAD,
            files=["main.kicad_pcb"],
        )
        u = self.understanding_engine.understand(
            "Update netclass Default track width to 0.3 mm and clearance to 0.25 mm on main.kicad_pcb",
            ctx,
        )
        self.assertTrue(u.modifies_files)
        plan = self.planner.create_plan(u, ctx)
        self.assertEqual(plan.validation_status, PlanValidationStatus.REQUIRES_APPROVAL)
        self.assertTrue(plan.requires_user_approval)
        self.assertEqual(plan.steps[0].tool_name, "kicad_update_netclass_design_rules")
        self.assertEqual(plan.steps[0].approval_status, ApprovalStatus.PENDING_APPROVAL)


class TestExecutionCoordinatorAndAnalyzer(unittest.TestCase):
    """Tests for Module 4 (Execution Coordinator) and Module 5 (Result Analyzer)."""

    def test_kicad_drc_execution_and_result_analysis(self) -> None:
        def fake_backend_executor(req: BackendExecutionRequest) -> BackendExecutionResponse:
            return BackendExecutionResponse(
                task_id="task_drc_001",
                status="completed",
                tool_name=req.tool_name,
                exit_code=5,
                stdout="Found 2 DRC violations",
                stderr="",
                structured_output={
                    "board_file": req.arguments.get("board_file"),
                    "violations": [
                        {
                            "type": "clearance",
                            "severity": "error",
                            "description": "Clearance violation (netclass 'Default' clearance 0.20 mm; actual 0.11 mm) between Track [VCC] and Pad U1-4 [GND]",
                            "file": "sensor_board.kicad_pcb",
                            "components": ["U1"],
                            "nets": ["VCC", "GND"],
                        },
                        {
                            "type": "courtyard_overlap",
                            "severity": "warning",
                            "description": "Courtyards overlap between footprint R1 and C2",
                            "file": "sensor_board.kicad_pcb",
                            "components": ["R1", "C2"],
                        },
                    ],
                },
            )

        backend = InProcessBackendAdapter(fake_backend_executor)
        agent = ForgeAgent(backend_client=backend)

        turn_req = AgentTurnRequest(
            query="Run DRC check on sensor_board.kicad_pcb",
            project_context=ProjectContext(
                project_id="proj_sensor",
                application=ApplicationType.KICAD,
                files=["sensor_board.kicad_pcb"],
            ),
        )
        resp = agent.handle_turn(turn_req)

        self.assertEqual(resp.status, "completed")
        self.assertIsNotNone(resp.analysis)
        assert resp.analysis is not None
        self.assertTrue(resp.analysis.tool_executed)
        self.assertTrue(resp.analysis.execution_succeeded)
        self.assertEqual(resp.analysis.error_count, 1)
        self.assertEqual(resp.analysis.warning_count, 1)
        self.assertEqual(len(resp.analysis.findings), 2)
        self.assertTrue(all(f.verified_from_tool for f in resp.analysis.findings))
        self.assertTrue(all(s.is_ai_suggestion for s in resp.analysis.ai_suggestions))
        self.assertIn("U1", resp.analysis.findings[0].components_or_nets)

    def test_rtl_verilator_lint_execution_and_analysis(self) -> None:
        verilator_stderr = (
            "%Warning-WIDTH: rtl/alu.v:27:8: Operator ASSIGN expects 8 bits on the Assign RHS, "
            "but Assign RHS's VARREF 'in_b' generates 4 bits.\n"
            "%Error: rtl/alu.v:45:12: Signal 'result_reg' is driven in combinational block without default assignment (inferred latch).\n"
        )

        def fake_rtl_executor(req: BackendExecutionRequest) -> BackendExecutionResponse:
            return BackendExecutionResponse(
                task_id="task_rtl_101",
                status="completed",
                tool_name=req.tool_name,
                exit_code=1,
                stdout="",
                stderr=verilator_stderr,
                structured_output={"source_files": req.arguments.get("source_files")},
            )

        agent = ForgeAgent(backend_client=InProcessBackendAdapter(fake_rtl_executor))
        resp = agent.handle_turn(
            AgentTurnRequest(
                query="Lint rtl/alu.v with Verilator",
                project_context=ProjectContext(
                    project_id="proj_alu",
                    application=ApplicationType.RTL,
                    files=["rtl/alu.v"],
                ),
            )
        )

        self.assertEqual(resp.status, "completed")
        assert resp.analysis is not None
        self.assertEqual(resp.analysis.error_count, 1)
        self.assertEqual(resp.analysis.warning_count, 1)
        self.assertEqual(resp.analysis.findings[0].file_path, "rtl/alu.v")
        self.assertEqual(resp.analysis.findings[0].line_number, 27)
        self.assertEqual(resp.analysis.findings[1].line_number, 45)
        self.assertGreaterEqual(len(resp.analysis.ai_suggestions), 2)

    def test_approval_workflow_blocks_until_user_approves(self) -> None:
        executed_calls = []

        def fake_modify_executor(req: BackendExecutionRequest) -> BackendExecutionResponse:
            executed_calls.append(req)
            return BackendExecutionResponse(
                task_id="task_mod_1",
                status="completed",
                tool_name=req.tool_name,
                exit_code=0,
                stdout="Updated netclass Default track_width=0.3mm",
                structured_output={"updated": True},
            )

        agent = ForgeAgent(backend_client=InProcessBackendAdapter(fake_modify_executor))

        # Step 1: Request modification WITHOUT approval -> must pause at awaiting_approval
        resp1 = agent.handle_turn(
            AgentTurnRequest(
                query="Update netclass Default track width to 0.3 mm on power.kicad_pcb",
                project_context=ProjectContext(
                    project_id="proj_pwr",
                    application=ApplicationType.KICAD,
                    files=["power.kicad_pcb"],
                ),
                user_approved_modifications=False,
            )
        )
        self.assertEqual(resp1.status, "awaiting_approval")
        self.assertEqual(len(executed_calls), 0)
        assert resp1.plan is not None

        # Step 2: Approve the plan and execute
        resp2 = agent.approve_and_execute(
            ApprovalDecisionRequest(
                plan_id=resp1.plan.plan_id,
                approved=True,
            )
        )
        self.assertEqual(resp2.status, "completed")
        self.assertEqual(len(executed_calls), 1)
        self.assertTrue(executed_calls[0].approved_by_user)

    def test_never_fabricates_success_when_backend_fails_or_times_out(self) -> None:
        def timeout_executor(req: BackendExecutionRequest) -> BackendExecutionResponse:
            raise TimeoutError("kicad-cli timed out after 30s")

        agent = ForgeAgent(backend_client=InProcessBackendAdapter(timeout_executor))
        resp = agent.handle_turn(
            AgentTurnRequest(
                query="Run DRC on board.kicad_pcb",
                project_context=ProjectContext(
                    project_id="proj_timeout",
                    application=ApplicationType.KICAD,
                    files=["board.kicad_pcb"],
                ),
            )
        )
        self.assertEqual(resp.status, "execution_failed")
        assert resp.analysis is not None
        self.assertFalse(resp.analysis.tool_executed)
        self.assertFalse(resp.analysis.execution_succeeded)

    def test_gemini_provider_model_normalization_and_secret_scrubbing(self) -> None:
        # Deprecated models are automatically upgraded to gemini-3.8-flash
        self.assertEqual(normalize_gemini_model_name("gemini-2.5-flash"), "gemini-3.8-flash")
        self.assertEqual(normalize_gemini_model_name("gemini-1.5-pro"), "gemini-3.8-flash")
        self.assertEqual(normalize_gemini_model_name("gemini-4-argon"), "gemini-4-argon")
        self.assertEqual(normalize_gemini_model_name("hydrogen"), "gemini-4-argon")
        self.assertEqual(normalize_gemini_model_name("gemini-4"), "gemini-4-argon")
        self.assertEqual(normalize_gemini_model_name("3.8"), "gemini-3.8-flash")

        # Secret scrubbing never leaks Gemini or Hydrogen API keys (AIza... or AQ....)
        fake_aiza_key = "AIzaSy" + "TestKeyOnly123456789012345"
        fake_aq_key = "AQ." + "TestTokenOnly123456789012345"
        provider = GeminiProvider(api_key=fake_aiza_key, model="hydrogen")
        self.assertEqual(provider.model, "gemini-4-argon")
        self.assertNotIn(fake_aiza_key, repr(provider))
        scrubbed = _scrub_secrets(
            f"Error calling endpoint with key={fake_aiza_key} and token={fake_aq_key}",
            fake_aiza_key,
        )
        self.assertNotIn(fake_aiza_key, scrubbed)
        self.assertNotIn(fake_aq_key, scrubbed)
        self.assertIn("[REDACTED_API_KEY]", scrubbed)

    def test_kicad_schematic_component_edit_requires_approval(self) -> None:
        agent = ForgeAgent()
        ctx = ProjectContext(
            project_id="proj_sch_edit",
            application=ApplicationType.KICAD,
            files=["power_supply.kicad_sch"],
        )
        resp = agent.handle_turn(
            AgentTurnRequest(
                query="Change R1 value to 4.7k on power_supply.kicad_sch",
                project_context=ctx,
                user_approved_modifications=False,
            )
        )
        self.assertEqual(resp.status, "awaiting_approval")
        assert resp.plan is not None
        self.assertEqual(resp.plan.steps[0].tool_name, "kicad_update_schematic_component")
        self.assertEqual(resp.plan.steps[0].arguments["reference"], "R1")
        self.assertEqual(resp.plan.steps[0].arguments["new_value"], "4.7k")
        self.assertTrue(resp.plan.requires_user_approval)

    def test_rtl_generate_module_requires_approval(self) -> None:
        agent = ForgeAgent()
        ctx = ProjectContext(
            project_id="proj_rtl_gen",
            application=ApplicationType.RTL,
            files=["rtl/counter.v"],
        )
        resp = agent.handle_turn(
            AgentTurnRequest(
                query="Generate an 8-bit synchronous counter module in rtl/counter.v",
                project_context=ctx,
                user_approved_modifications=False,
            )
        )
        self.assertEqual(resp.status, "awaiting_approval")
        assert resp.plan is not None
        self.assertEqual(resp.plan.steps[0].tool_name, "rtl_generate_module")
        self.assertEqual(resp.plan.steps[0].arguments["target_file"], "rtl/counter.v")
        self.assertIn("module counter", resp.plan.steps[0].arguments["generated_code"])

    def test_propose_fix_and_reverify_plan_from_analysis(self) -> None:
        def fake_rtl_executor(req: BackendExecutionRequest) -> BackendExecutionResponse:
            return BackendExecutionResponse(
                task_id="task_lint_fix",
                status="completed",
                tool_name=req.tool_name,
                exit_code=1,
                stdout="",
                stderr="%Warning-WIDTH: rtl/alu.v:18:5: Operator ASSIGN expects 8 bits on the Assign RHS.\n",
                structured_output={"source_files": ["rtl/alu.v"]},
            )

        agent = ForgeAgent(backend_client=InProcessBackendAdapter(fake_rtl_executor))
        ctx = ProjectContext(
            project_id="proj_alu_fix",
            application=ApplicationType.RTL,
            files=["rtl/alu.v"],
        )
        turn_resp = agent.handle_turn(
            AgentTurnRequest(query="Lint rtl/alu.v", project_context=ctx)
        )
        assert turn_resp.analysis is not None

        fix_plan = agent.propose_fix_plan(turn_resp.analysis, ctx)
        self.assertIsNotNone(fix_plan)
        assert fix_plan is not None
        self.assertTrue(fix_plan.requires_user_approval)
        self.assertEqual(len(fix_plan.steps), 2)
        self.assertEqual(fix_plan.steps[0].tool_name, "rtl_apply_patch")
        self.assertEqual(fix_plan.steps[1].tool_name, "rtl_run_lint")
        self.assertEqual(fix_plan.steps[1].depends_on, [1])


if __name__ == "__main__":
    unittest.main()
