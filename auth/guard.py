"""
Anti-tamper checks.
Called once at startup before any UI is shown.
Exits immediately if a debugger or known RE tool is detected.
"""

import ctypes
import ctypes.wintypes
import sys
import os
import subprocess
import time


def _debugger_present() -> bool:
    """IsDebuggerPresent — catches most user-mode debuggers."""
    try:
        return bool(ctypes.windll.kernel32.IsDebuggerPresent())
    except Exception:
        return False


def _remote_debugger_present() -> bool:
    """CheckRemoteDebuggerPresent — catches remote attachers."""
    try:
        result = ctypes.wintypes.BOOL(False)
        ctypes.windll.kernel32.CheckRemoteDebuggerPresent(
            ctypes.windll.kernel32.GetCurrentProcess(),
            ctypes.byref(result))
        return bool(result)
    except Exception:
        return False


def _nt_debug_port() -> bool:
    """
    NtQueryInformationProcess with ProcessDebugPort (7).
    Returns non-zero if a kernel debugger is attached.
    """
    try:
        ntdll = ctypes.windll.ntdll
        NtQueryInformationProcess = ntdll.NtQueryInformationProcess
        debug_port = ctypes.c_ulong(0)
        status = NtQueryInformationProcess(
            ctypes.windll.kernel32.GetCurrentProcess(),
            7,   # ProcessDebugPort
            ctypes.byref(debug_port),
            ctypes.sizeof(debug_port),
            None)
        return status == 0 and debug_port.value != 0
    except Exception:
        return False


# Known reverse engineering tools (process names, lowercase)
_RE_TOOLS = {
    "ollydbg.exe", "x32dbg.exe", "x64dbg.exe", "windbg.exe",
    "ida.exe", "ida64.exe", "idaq.exe", "idaq64.exe",
    "idaw.exe", "idaw64.exe",
    "radare2.exe", "r2.exe",
    "ghidra.exe",
    "cheatengine.exe", "cheatengine-x86_64.exe",
    "processhacker.exe", "procmon.exe", "procmon64.exe",
    "wireshark.exe", "fiddler.exe", "charles.exe",
    "httpdebugger.exe", "mitmproxy.exe",
    "pyinstxtractor.py",
}


def _re_tool_running() -> bool:
    """Check tasklist for known RE/debugging processes."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/fo", "csv", "/nh"],
            creationflags=0x08000000, timeout=4
        ).decode(errors="ignore").lower()
        for tool in _RE_TOOLS:
            if tool.split(".")[0] in out:   # match on name without extension
                return True
    except Exception:
        pass
    return False


def _timing_check() -> bool:
    """
    Simple RDTSC-style timing check.
    Debuggers slow down execution; if a tight loop takes too long
    we're likely being stepped through.
    """
    try:
        t0 = time.perf_counter_ns()
        x  = 0
        for _ in range(100_000):
            x ^= _
        t1 = time.perf_counter_ns()
        elapsed_ms = (t1 - t0) / 1_000_000
        return elapsed_ms > 2000   # > 2 s for 100k iterations = debugger
    except Exception:
        return False


def run_checks(silent: bool = False) -> bool:
    """
    Run all anti-tamper checks.
    Returns True if the environment is clean.
    If silent=False, shows a message box and exits on detection.
    """
    detections: list[str] = []

    if _debugger_present():
        detections.append("debugger (IsDebuggerPresent)")
    if _remote_debugger_present():
        detections.append("remote debugger")
    if _nt_debug_port():
        detections.append("kernel debug port")
    if _re_tool_running():
        detections.append("reverse engineering tool")
    if _timing_check():
        detections.append("timing anomaly")

    if detections:
        if not silent:
            try:
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "SteamGuard cannot run in this environment.\n\n"
                    "Please close any debugging or analysis tools and try again.",
                    "SteamGuard — Security Check Failed",
                    0x10)  # MB_ICONERROR
            except Exception:
                pass
        sys.exit(1)

    return True
