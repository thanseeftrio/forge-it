"""
FORGE IT - IC / RTL Integration Package.
Exposes ICAdapter, ICDetector, RTLProjectInspector, and ICOperations.
"""

from integrations.ic.detector import ICDetector
from integrations.ic.ic_adapter import ICAdapter
from integrations.ic.operations import ICOperations
from integrations.ic.project_inspector import RTLProjectInspector

__all__ = [
    "ICAdapter",
    "ICDetector",
    "RTLProjectInspector",
    "ICOperations"
]
