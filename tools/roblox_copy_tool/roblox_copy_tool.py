"""Rivvak Roblox Copier — single-file entrypoint for Nuitka --onefile.

This tool is a local-only helper for copying assets between Roblox accounts
using the user's OWN .ROBLOSECURITY cookie (typed in manually). It does not
read the Windows registry, ship credentials to any third party, or use any
external auth service.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _resource_root() -> Path:
    """Return the directory where LuaScript.lua lives (works in Nuitka --onefile)."""
    # Nuitka onefile extracts data files next to the executable at runtime.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# Make sibling modules importable when frozen or when run directly.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Expose the Lua script location to the rest of the package.
os.environ.setdefault("ROBLOX_COPIER_RESOURCE_DIR", str(_resource_root()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rivvak Roblox Copier")
    parser.add_argument(
        "--server",
        action="store_true",
        help="Run the local HTTP server without the GUI",
    )
    parser.add_argument(
        "--cookie",
        default="",
        help=".ROBLOSECURITY cookie for --server mode",
    )
    parser.add_argument(
        "--group-id",
        type=int,
        default=None,
        help="Optional Roblox group ID",
    )
    args = parser.parse_args(argv)

    if args.server:
        from server import serve  # local import so --help stays fast
        serve(cookie=args.cookie, group_id=args.group_id)
        return 0

    from ui import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
