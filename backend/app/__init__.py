import sys
from pathlib import Path

# Ensure both backend directory and repo root are in sys.path so imports work
# whether invoked from repository root or from inside backend/
_backend_dir = str(Path(__file__).resolve().parent.parent)
_repo_root = str(Path(__file__).resolve().parent.parent.parent)

for _p in [_repo_root, _backend_dir]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
