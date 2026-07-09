"""AI-agent guardrails: single source of truth for what an autonomous agent may
touch in this repo."""

from .path_rules import ALLOW_DIRECT_PATTERNS, DENY_PATTERNS
from .diff_check import check_diff, classify_diff, ClassificationResult

__all__ = [
    "ALLOW_DIRECT_PATTERNS",
    "DENY_PATTERNS",
    "check_diff",
    "classify_diff",
    "ClassificationResult",
]
