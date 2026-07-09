"""Path allow/deny rules for autonomous AI agents.

**Deny takes precedence.** If any changed file matches DENY_PATTERNS the diff is
refused outright. If **all** changed files match ALLOW_DIRECT_PATTERNS the
agent may push directly to `main`; otherwise a PR is required.

Patterns use `fnmatch`-style globs with `**` (via `pathspec` when available;
falls back to a hand-rolled matcher). Paths are always compared as
forward-slash POSIX strings relative to the repo root.

This file is the single source of truth. It is imported by:
  - deploy/devbox/scripts/path-guard.py  (on the VM, blocks bad pushes)
  - .github/workflows/heal/verify-agent-pr.yml (defense in depth in CI)
"""

from __future__ import annotations

# ── Never-touch: any diff touching these fails immediately ────────────────────
DENY_PATTERNS: tuple[str, ...] = (
    # License / auth / entitlement code
    "auth/**",
    "server/**/license*",
    "server/**/entitlement*",
    "**/hwid*.py",
    "**/cert*.py",
    "**/pinning*.py",
    # Secrets and keys
    ".env*",
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    # CI / build infrastructure (only humans edit these)
    ".github/workflows/**",
    "build.bat",
    "Dockerfile",
    "server/Dockerfile",
    "scripts/build*",
    # Cert pin manifest served to clients
    "hashes.txt",
)

# ── Auto-commit-to-main: any diff whose files are ALL in this set may push
#    directly. Anything else must go through a PR. ────────────────────────────
ALLOW_DIRECT_PATTERNS: tuple[str, ...] = (
    "docs/**",
    "tests/**",
    "**/*.md",
    "requirements.txt",
    "requirements*.txt",
    "server/requirements.txt",
    "server/bot_requirements.txt",
    "pyproject.toml",
    "dashboard/**",
)


def _match(pattern: str, path: str) -> bool:
    """POSIX-style glob match with `**`. Tries `pathspec` (Git-native semantics)
    then falls back to a fnmatch-based recursive walker."""
    try:
        import pathspec  # type: ignore

        spec = pathspec.PathSpec.from_lines("gitwildmatch", [pattern])
        return spec.match_file(path)
    except Exception:
        # Fallback: split ** into components, match each segment with fnmatch.
        import fnmatch

        # fnmatch doesn't treat ** specially, so expand: "a/**/b" matches
        # "a/b", "a/x/b", "a/x/y/b", etc. Approach: replace "**" with "*",
        # then also try matching with "**" stripped entirely.
        candidates = {
            pattern,
            pattern.replace("/**/", "/"),
            pattern.replace("**/", ""),
            pattern.replace("/**", ""),
            pattern.replace("**", "*"),
        }
        for cand in candidates:
            if fnmatch.fnmatch(path, cand):
                return True
        return False


def is_denied(path: str) -> bool:
    """Return True iff `path` matches any DENY pattern."""
    return any(_match(p, path) for p in DENY_PATTERNS)


def is_allow_direct(path: str) -> bool:
    """Return True iff `path` matches any ALLOW_DIRECT pattern (and is not denied)."""
    if is_denied(path):
        return False
    return any(_match(p, path) for p in ALLOW_DIRECT_PATTERNS)
