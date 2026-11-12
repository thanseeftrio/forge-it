import os
from pathlib import Path
from typing import List, Optional, Tuple
from backend.app.core.security import validate_project_path
from backend.app.schemas.project import ProjectFileInfo, ProjectInspectResponse


class ProjectService:
    """
    Validates, scans, and classifies user project workspaces (KiCad PCB or Verilog RTL).
    """

    PCB_EXTENSIONS = {".kicad_pro", ".kicad_pcb", ".kicad_sch", ".pro", ".sch", ".kicad_dru"}
    IC_EXTENSIONS = {".v", ".sv", ".vh", ".svh", ".vhd", ".vhdl"}

    def _delegate_to_adapter(self, target_path: Path, project_type: str) -> Optional[ProjectInspectResponse]:
        """
        Check if Member 4 (PCB) or Member 5 (IC) project inspector is implemented.
        """
        try:
            if project_type == "pcb":
                from integrations.pcb import project_inspector  # type: ignore
                if hasattr(project_inspector, "inspect"):
                    return project_inspector.inspect(str(target_path))
            elif project_type == "ic":
                from integrations.ic import project_inspector  # type: ignore
                if hasattr(project_inspector, "inspect"):
                    return project_inspector.inspect(str(target_path))
        except Exception:
            return None
        return None

    def inspect_project(self, raw_path: str) -> ProjectInspectResponse:
        """
        Validate path and inspect project contents.
        """
        valid_path = validate_project_path(raw_path)
        project_name = valid_path.name

        files: List[ProjectFileInfo] = []
        main_files: List[str] = []
        pcb_file_count = 0
        ic_file_count = 0

        if valid_path.is_file():
            stat = valid_path.stat()
            ext = valid_path.suffix.lower()
            files.append(
                ProjectFileInfo(
                    path=str(valid_path),
                    name=valid_path.name,
                    size_bytes=stat.st_size,
                    file_type=ext,
                    is_directory=False,
                )
            )
            main_files.append(valid_path.name)
            if ext in self.PCB_EXTENSIONS:
                pcb_file_count += 1
            elif ext in self.IC_EXTENSIONS:
                ic_file_count += 1
        else:
            # Walk directory (limit depth/file count to prevent runaway scans)
            max_files = 200
            count = 0
            for root, _, filenames in os.walk(valid_path):
                # Ignore hidden dirs like .git
                if any(part.startswith(".") for part in Path(root).parts):
                    continue

                for fname in filenames:
                    if fname.startswith("."):
                        continue

                    fpath = Path(root) / fname
                    try:
                        stat = fpath.stat()
                        ext = fpath.suffix.lower()
                        rel_path = str(fpath.relative_to(valid_path))

                        files.append(
                            ProjectFileInfo(
                                path=rel_path,
                                name=fname,
                                size_bytes=stat.st_size,
                                file_type=ext,
                                is_directory=False,
                            )
                        )

                        if ext in self.PCB_EXTENSIONS:
                            pcb_file_count += 1
                            if ext in {".kicad_pro", ".kicad_pcb", ".pro"}:
                                main_files.append(rel_path)
                        elif ext in self.IC_EXTENSIONS:
                            ic_file_count += 1
                            main_files.append(rel_path)

                        count += 1
                        if count >= max_files:
                            break
                    except OSError:
                        continue

                if count >= max_files:
                    break

        # Determine project type
        if pcb_file_count > 0 and pcb_file_count >= ic_file_count:
            project_type = "pcb"
            summary = f"Detected KiCad PCB design with {len(files)} files."
            supported_ops = ["inspect_project", "kicad_drc", "kicad_erc", "kicad_export_gerbers"]
        elif ic_file_count > 0:
            project_type = "ic"
            summary = f"Detected Verilog/SystemVerilog RTL project with {len(files)} files."
            supported_ops = ["inspect_project", "verilator_lint", "verilator_compile"]
        else:
            project_type = "unknown"
            summary = f"Scanned project with {len(files)} files. No recognized KiCad or Verilog files found."
            supported_ops = ["inspect_project"]

        # Check if a custom team adapter handles inspection
        adapter_res = self._delegate_to_adapter(valid_path, project_type)
        if adapter_res is not None:
            return adapter_res

        return ProjectInspectResponse(
            project_path=str(valid_path),
            project_name=project_name,
            project_type=project_type,
            main_files=main_files,
            files=files,
            summary=summary,
            supported_operations=supported_ops,
        )


project_service = ProjectService()
