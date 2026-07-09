#!/usr/bin/env python3
"""CLI wrapper around ai.guards.diff_check for shell use on sg-devbox.

Exits 0 if all files may be committed directly, 2 if a PR is required, 3 if
the diff must be refused, 1 on internal error.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("/home/sgagent/SteamGuard")
sys.path.insert(0, str(REPO))

try:
    from ai.guards.diff_check import classify_diff, EXIT_DIRECT, EXIT_PR, EXIT_DENIED
except Exception as e:
    print(f"path-guard: could not import guards: {e}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: path-guard.py <worktree>", file=sys.stderr)
        return 1
    wt = Path(sys.argv[1])
    if not wt.is_dir():
        print(f"path-guard: not a directory: {wt}", file=sys.stderr)
        return 1

    diff = subprocess.run(
        ["git", "diff", "--name-only"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        print(f"path-guard: git diff failed: {diff.stderr}", file=sys.stderr)
        return 1

    files = [ln for ln in diff.stdout.splitlines() if ln.strip()]
    if not files:
        print("path-guard: no changes")
        return EXIT_DIRECT

    result = classify_diff(files)
    print(result.summary())
    return {"direct": EXIT_DIRECT, "pr": EXIT_PR, "denied": EXIT_DENIED}[result.classification]


if __name__ == "__main__":
    sys.exit(main())
