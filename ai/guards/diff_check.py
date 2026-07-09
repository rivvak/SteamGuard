"""Classify a git diff against the allow/deny rules.

Result semantics:

    denied     — At least one changed file matches DENY. Refuse the change.
    direct     — Every changed file matches ALLOW_DIRECT. Push to main.
    pr         — Some files are outside ALLOW_DIRECT but none are denied. PR.

Callable from Python (used by the VM's `run-session.sh` and by CI). Also
runnable as a CLI:

    python -m ai.guards.diff_check --files a.py b.md      -> exit 0/2/3
    git diff --name-only main | python -m ai.guards.diff_check --stdin
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from typing import Iterable, Literal

from .path_rules import is_allow_direct, is_denied

Classification = Literal["denied", "direct", "pr"]

EXIT_DIRECT = 0
EXIT_PR = 2
EXIT_DENIED = 3


@dataclass
class ClassificationResult:
    classification: Classification
    denied_files: list[str] = field(default_factory=list)
    direct_files: list[str] = field(default_factory=list)
    pr_files: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"classification: {self.classification}"]
        if self.denied_files:
            lines.append("denied:")
            lines.extend(f"  - {p}" for p in self.denied_files)
        if self.direct_files:
            lines.append("direct:")
            lines.extend(f"  - {p}" for p in self.direct_files)
        if self.pr_files:
            lines.append("pr:")
            lines.extend(f"  - {p}" for p in self.pr_files)
        return "\n".join(lines)


def classify_diff(paths: Iterable[str]) -> ClassificationResult:
    denied: list[str] = []
    direct: list[str] = []
    pr: list[str] = []

    for raw in paths:
        p = raw.strip()
        if not p:
            continue
        if is_denied(p):
            denied.append(p)
        elif is_allow_direct(p):
            direct.append(p)
        else:
            pr.append(p)

    if denied:
        return ClassificationResult("denied", denied, direct, pr)
    if not pr:
        return ClassificationResult("direct", denied, direct, pr)
    return ClassificationResult("pr", denied, direct, pr)


def check_diff(paths: Iterable[str]) -> ClassificationResult:
    """Alias for `classify_diff` (kept for symmetry with the guardrails API)."""
    return classify_diff(paths)


def _main() -> int:
    ap = argparse.ArgumentParser(description="Classify a git diff against the guardrail rules.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--files", nargs="+", help="File paths to check")
    g.add_argument("--stdin", action="store_true", help="Read paths from stdin (one per line)")
    args = ap.parse_args()

    if args.stdin:
        paths = [ln.strip() for ln in sys.stdin if ln.strip()]
    else:
        paths = list(args.files)

    result = classify_diff(paths)
    print(result.summary())

    return {
        "direct": EXIT_DIRECT,
        "pr": EXIT_PR,
        "denied": EXIT_DENIED,
    }[result.classification]


if __name__ == "__main__":
    sys.exit(_main())
