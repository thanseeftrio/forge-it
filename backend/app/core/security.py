from pathlib import Path
from typing import List, Optional
from backend.app.core.config import settings


class SecurityError(Exception):
    """Raised when an operation or path violates security constraints."""
    pass


FORBIDDEN_SYSTEM_PATHS = {
    Path("/"),
    Path("/etc"),
    Path("/root"),
    Path("/bin"),
    Path("/sbin"),
    Path("/usr/bin"),
    Path("/usr/sbin"),
    Path("/dev"),
    Path("/sys"),
    Path("/proc"),
    Path("/boot"),
}


def validate_project_path(raw_path: str, base_dir: Optional[Path] = None) -> Path:
    """
    Validate that the project path is safe to access:
    - Resolves absolute path.
    - Prevents access to critical system directories.
    - Verifies the path actually exists on the filesystem.
    """
    if not raw_path or not raw_path.strip():
        raise SecurityError("Project path cannot be empty.")

    target = Path(raw_path.strip())

    # If relative, resolve relative to base_dir or current working directory
    if not target.is_absolute():
        base = base_dir or settings.BASE_DIR
        target = (base / target).resolve()
    else:
        target = target.resolve()

    # Block access to system critical directories
    for forbidden in FORBIDDEN_SYSTEM_PATHS:
        if target == forbidden or (forbidden != Path("/") and forbidden in target.parents):
            raise SecurityError(f"Access to system directory '{target}' is strictly forbidden.")

    if not target.exists():
        raise FileNotFoundError(f"Project path does not exist: {target}")

    return target


def is_operation_allowed(operation: str) -> bool:
    """Verify if the requested operation is in the allowed operations whitelist."""
    return operation in settings.ALLOWED_OPERATIONS


def validate_operation_safety(operation: str, requires_approval: bool, approved: bool) -> None:
    """
    Ensure the operation is permitted and has user approval if required.
    """
    if not is_operation_allowed(operation):
        raise SecurityError(
            f"Operation '{operation}' is not permitted. "
            f"Allowed operations: {', '.join(settings.ALLOWED_OPERATIONS)}"
        )

    if requires_approval and not approved:
        raise SecurityError(
            f"Operation '{operation}' requires explicit user approval before execution."
        )


def sanitize_command_args(args: List[str]) -> List[str]:
    """
    Sanitize argument list ensuring all elements are strings and reject dangerous shell operators.
    """
    sanitized: List[str] = []
    forbidden_tokens = {";", "&&", "||", "|", "`", "$", "\n", "\r"}

    for arg in args:
        arg_str = str(arg)
        for token in forbidden_tokens:
            if token in arg_str:
                raise SecurityError(f"Argument contains forbidden token '{token}': {arg_str}")
        sanitized.append(arg_str)

    return sanitized

