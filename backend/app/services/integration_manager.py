import os
import shutil
import subprocess
from typing import Dict, List, Optional
from backend.app.core.config import settings
from backend.app.schemas.integration import IntegrationInfo


class IntegrationManager:
    """
    Manages detection and communication with external EDA tools and team adapters.
    """

    def __init__(self):
        self._definitions: Dict[str, Dict] = {
            "kicad": {
                "name": "KiCad EDA",
                "division": "pcb",
                "description": "Printed Circuit Board design suite CLI for DRC/ERC and export operations.",
                "binary_name": settings.KICAD_CLI_PATH,
                "supported_extensions": [".kicad_pro", ".kicad_pcb", ".kicad_sch", ".pro"],
                "supported_operations": ["inspect_project", "kicad_drc", "kicad_erc", "kicad_export_gerbers"],
                "version_flag": ["version"],
            },
            "verilator": {
                "name": "Verilator RTL Simulator",
                "division": "ic",
                "description": "High-performance Verilog/SystemVerilog simulator and linter.",
                "binary_name": settings.VERILATOR_PATH,
                "supported_extensions": [".v", ".sv", ".vh", ".svh"],
                "supported_operations": ["inspect_project", "verilator_lint", "verilator_compile"],
                "version_flag": ["--version"],
            },
        }

    def _check_team_adapter(self, division: str) -> bool:
        """
        Check if Member 4 (PCB) or Member 5 (IC) adapters are available and have implementations.
        """
        try:
            if division == "pcb":
                import integrations.pcb.detector as pcb_detector  # type: ignore
                return hasattr(pcb_detector, "detect_kicad")
            elif division == "ic":
                import integrations.ic.detector as ic_detector  # type: ignore
                return hasattr(ic_detector, "detect_verilator")
        except (ImportError, AttributeError):
            return False
        return False

    def _detect_system_binary(self, binary_name: str, version_args: List[str]) -> tuple[bool, Optional[str], Optional[str]]:
        """
        Safely check if an executable binary is present in PATH and query its version.
        Uses subprocess with shell=False and safe argument arrays.
        """
        resolved_path = shutil.which(binary_name)
        if not resolved_path:
            return False, None, None

        version_str: Optional[str] = None
        try:
            cmd = [resolved_path] + version_args
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
            if result.returncode == 0:
                version_str = result.stdout.strip().splitlines()[0] if result.stdout else None
        except Exception:
            # Tool exists on disk but failed to report version
            version_str = "Installed (version query failed)"

        return True, resolved_path, version_str

    def get_all_integrations(self) -> List[IntegrationInfo]:
        """Return list of all registered integrations with their current detection status."""
        results: List[IntegrationInfo] = []
        for int_id, meta in self._definitions.items():
            installed, exe_path, version = self._detect_system_binary(
                meta["binary_name"], meta["version_flag"]
            )
            has_adapter = self._check_team_adapter(meta["division"])

            info = IntegrationInfo(
                id=int_id,
                name=meta["name"],
                division=meta["division"],
                description=meta["description"],
                binary_name=meta["binary_name"],
                installed=installed,
                version=version,
                executable_path=exe_path,
                supported_extensions=meta["supported_extensions"],
                supported_operations=meta["supported_operations"],
                adapter_available=has_adapter,
            )
            results.append(info)
        return results

    def detect_integrations(self, division: Optional[str] = None) -> List[IntegrationInfo]:
        """Detect integrations filtered by optional division."""
        all_integrations = self.get_all_integrations()
        if division:
            division_lower = division.lower().strip()
            return [item for item in all_integrations if item.division == division_lower]
        return all_integrations


integration_manager = IntegrationManager()

