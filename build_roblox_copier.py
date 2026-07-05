"""Build roblox_copier.exe with PyInstaller."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "roblox_copier",
        "--add-data",
        f"{ROOT / 'roblox_copier' / 'LuaScript.lua'}{';' if sys.platform == 'win32' else ':'}roblox_copier",
        str(ROOT / "roblox_copier" / "main.py"),
    ]
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
