import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from backend.app.core.config import settings
from backend.app.schemas.agent import PlanRequest, PlanResponse, PlanStep
from backend.app.services.project_service import project_service

# Ensure workspace root is in sys.path to dynamically import from ai-agent when ready
WORKSPACE_ROOT = str(settings.BASE_DIR)
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)


class AIAgentService:
    """
    Connects Backend to the AI Agent (Member 3) and Gemini API.
    Priority:
    1. Member 3 planner module (`ai-agent/planner.py`) if implemented.
    2. Google Gemini API via `google-genai` SDK when `GEMINI_API_KEY` is present.
    3. Fallback deterministic rule-based planner.
    """

    def _call_member3_planner(self, instruction: str, project_data: dict) -> Optional[List[PlanStep]]:
        """
        Attempt to delegate planning to ai-agent module if implemented by Member 3.
        """
        try:
            import importlib
            ai_planner = importlib.import_module("ai-agent.planner")
            if hasattr(ai_planner, "generate_plan"):
                raw_steps = ai_planner.generate_plan(instruction, project_data)
                return [PlanStep(**s) for s in raw_steps]
        except Exception:
            return None
        return None

    def _call_gemini_planner(self, instruction: str, project_data: dict) -> Optional[tuple[str, List[PlanStep]]]:
        """
        Call Gemini using official google-genai SDK to generate a safe execution plan.
        """
        api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            return None

        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)

            allowed_ops = settings.ALLOWED_OPERATIONS
            prompt = f"""
You are the AI Planner for FORGE IT, an engineering workspace assistant for PCB (KiCad) and IC (Verilog/RTL) workflows.
Generate a structured JSON execution plan for the user instruction.

Allowed operations strictly limited to: {json.dumps(allowed_ops)}

Target Project Info:
- Name: {project_data.get('project_name')}
- Type: {project_data.get('project_type')}
- Main Files: {project_data.get('main_files')}
- Supported Operations: {project_data.get('supported_operations')}

User Instruction: "{instruction}"

Output valid JSON matching this exact structure:
{{
  "summary": "Short explanation of the planned workflow",
  "steps": [
    {{
      "step_number": 1,
      "application": "KiCad or Verilator or ForgeBackend",
      "operation": "one of the allowed operations",
      "parameters": {{}},
      "description": "What this step performs",
      "requires_approval": true or false,
      "is_destructive": false
    }}
  ]
}}
Do NOT output code blocks or markdown, output pure JSON only.
"""
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )

            if not response.text:
                return None

            data = json.loads(response.text)
            summary = data.get("summary", f"AI-generated plan for: '{instruction}'")
            raw_steps = data.get("steps", [])

            valid_steps: List[PlanStep] = []
            for step_data in raw_steps:
                op = step_data.get("operation")
                # Filter strictly against allowed operations
                if op in settings.ALLOWED_OPERATIONS:
                    # Enforce approval for engineering tool runs
                    if op != "inspect_project":
                        step_data["requires_approval"] = True
                    valid_steps.append(PlanStep(**step_data))

            if valid_steps:
                return summary, valid_steps

        except Exception as e:
            # Fall back safely if API call fails
            return None

        return None

    def create_plan(self, request: PlanRequest) -> PlanResponse:
        """
        Produce a structured execution plan for the requested instruction.
        """
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"

        # 1. Inspect project first to gather context
        project_info = project_service.inspect_project(request.project_path)
        project_data = project_info.model_dump()

        # 2. Check Member 3 planner module first
        steps = self._call_member3_planner(request.instruction, project_data)
        if steps:
            return PlanResponse(
                plan_id=plan_id,
                project_path=str(project_info.project_path),
                instruction=request.instruction,
                summary=f"Team AI Agent plan for: '{request.instruction}'",
                steps=steps,
                requires_approval=any(s.requires_approval for s in steps),
                agent_provider="ai-agent (Member 3)",
            )

        # 3. Call Gemini if API key is configured
        gemini_result = self._call_gemini_planner(request.instruction, project_data)
        if gemini_result:
            summary, gemini_steps = gemini_result
            return PlanResponse(
                plan_id=plan_id,
                project_path=str(project_info.project_path),
                instruction=request.instruction,
                summary=summary,
                steps=gemini_steps,
                requires_approval=any(s.requires_approval for s in gemini_steps),
                agent_provider=f"Gemini ({settings.GEMINI_MODEL})",
            )

        # 4. Fallback deterministic rule-based planner
        steps = []
        p_type = project_info.project_type
        inst_lower = request.instruction.lower()

        if p_type == "pcb" or "pcb" in inst_lower or "drc" in inst_lower:
            steps.append(
                PlanStep(
                    step_number=1,
                    application="ForgeBackend",
                    operation="inspect_project",
                    parameters={"project_path": request.project_path},
                    description="Inspect KiCad project tree and verify board layout files.",
                    requires_approval=False,
                )
            )
            steps.append(
                PlanStep(
                    step_number=2,
                    application="KiCad",
                    operation="kicad_drc",
                    parameters={"project_path": request.project_path},
                    description="Run KiCad Design Rules Check (DRC) and extract violations.",
                    requires_approval=True,
                )
            )
            summary = f"Generated 2-step plan to inspect PCB and run KiCad DRC for: '{request.instruction}'"
        elif p_type == "ic" or "verilog" in inst_lower or "lint" in inst_lower or "rtl" in inst_lower:
            steps.append(
                PlanStep(
                    step_number=1,
                    application="ForgeBackend",
                    operation="inspect_project",
                    parameters={"project_path": request.project_path},
                    description="Scan RTL hierarchy and discover Verilog modules.",
                    requires_approval=False,
                )
            )
            steps.append(
                PlanStep(
                    step_number=2,
                    application="Verilator",
                    operation="verilator_lint",
                    parameters={"project_path": request.project_path},
                    description="Run Verilator linting against RTL files.",
                    requires_approval=True,
                )
            )
            summary = f"Generated 2-step plan to inspect RTL and run Verilator lint for: '{request.instruction}'"
        else:
            steps.append(
                PlanStep(
                    step_number=1,
                    application="ForgeBackend",
                    operation="inspect_project",
                    parameters={"project_path": request.project_path},
                    description="Inspect project directory structure.",
                    requires_approval=False,
                )
            )
            summary = f"Default inspection plan for: '{request.instruction}'"

        return PlanResponse(
            plan_id=plan_id,
            project_path=str(project_info.project_path),
            instruction=request.instruction,
            summary=summary,
            steps=steps,
            requires_approval=any(s.requires_approval for s in steps),
            agent_provider="forge-rule-planner",
        )


ai_service = AIAgentService()
