"""Day09 student starter kit."""

VARIANT_ID = "l3b"
OUTPUT_SCHEMA_VERSION = "day09-l3b-output-v2"

from .builder import build_l3b_output, save_case_output  # noqa: E402
from .conflict_resolver import ConflictResolver  # noqa: E402
from .verifier import InvariantViolationError, VerificationReport, Verifier  # noqa: E402

__all__ = [
    "OUTPUT_SCHEMA_VERSION",
    "VARIANT_ID",
    "ConflictResolver",
    "InvariantViolationError",
    "VerificationReport",
    "Verifier",
    "build_l3b_output",
    "save_case_output",
]
