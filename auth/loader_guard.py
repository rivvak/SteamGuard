"""
loader_guard.py — Anti-tamper module for the SteamGuard Loader.

Called once at startup (before any UI) to detect debuggers, known
reverse-engineering / analysis tools, and virtual-machine environments.
All checks fail closed but never raise — a missing dependency or an
unexpected platform simply yields a benign result.
"""

import sys
import os
import hashlib
import ctypes
import subprocess

BANNED_PROCESSES = [
    "x64dbg.exe", "x32dbg.exe", "ollydbg.exe", "ida64.exe", "ida.exe",
    "idaq.exe", "idaq64.exe", "cheatengine.exe", "cheatengine-x86_64.exe",
    "processhacker.exe", "wireshark.exe", "fiddler.exe", "httpdebugger.exe",
    "charles.exe", "mitmproxy.exe", "relyze.exe", "ghidra.exe",
]


def check_debugger() -> bool:
    """Returns True if a debugger is attached."""
    try:
        return bool(ctypes.windll.kernel32.IsDebuggerPresent())
    except Exception:
        return False


def check_bad_processes() -> list:
    """Returns list of any banned analysis tools currently running."""
    found = []
    try:
        out = subprocess.check_output(
            ["tasklist", "/fo", "csv", "/nh"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode(errors="ignore").lower()
        for proc in BANNED_PROCESSES:
            if proc.lower() in out:
                found.append(proc)
    except Exception:
        pass
    return found


def check_vm() -> bool:
    """Returns True if running in a VM."""
    import winreg
    needles = ("VBOX", "VMWARE", "QEMU", "VIRTUAL", "BOCHS", "INNOTEK", "KVM", "HYPERV")
    try:
        for path in (r"HARDWARE\DESCRIPTION\System", r"HARDWARE\DESCRIPTION\System\BIOS"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
                    for name in ("SystemBiosVersion", "SystemManufacturer",
                                 "SystemProductName", "VideoBiosVersion"):
                        try:
                            val = str(winreg.QueryValueEx(k, name)[0]).upper()
                            if any(n in val for n in needles):
                                return True
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        pass
    return False


def self_hash() -> str:
    """Hash the running loader executable."""
    try:
        exe = sys.executable if getattr(sys, 'frozen', False) else __file__
        return hashlib.sha256(open(exe, 'rb').read()).hexdigest()
    except Exception:
        return ""


def run_checks(silent: bool = False) -> dict:
    """Run all anti-tamper checks. Returns dict of results."""
    results = {
        "debugger": check_debugger(),
        "bad_processes": check_bad_processes(),
        "vm": check_vm(),
        "hash": self_hash(),
    }
    return results
