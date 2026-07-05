from __future__ import annotations

import argparse
from typing import Optional

from .server import serve
from .ui import run


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Rivvak Roblox Copier")
    parser.add_argument("--server", action="store_true", help="Run the local HTTP server without the GUI")
    parser.add_argument("--cookie", default="", help=".ROBLOSECURITY cookie for --server mode")
    parser.add_argument("--group-id", type=int, default=None, help="Optional Roblox group ID")
    args = parser.parse_args(argv)
    if args.server:
        serve(cookie=args.cookie, group_id=args.group_id)
        return 0
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
