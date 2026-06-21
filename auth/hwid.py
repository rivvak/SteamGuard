"""
Hardware ID fingerprinting.
Combines Windows Machine GUID + CPU Processor ID + C: Volume Serial.
All three hashed together into a single SHA-256 string.
The hash is what gets sent to the server — raw hardware data never leaves the machine.
"""

import hashlib
import winreg
import subprocess
import re


def _machine_guid() -> str:
    """Windows Machine GUID — generated at install, very stable."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography")
        value, _ = winreg.QueryValueEx(key, "MachineGuid")
        winreg.CloseKey(key)
        return value.strip()
    except Exception:
        return ""


def _cpu_id() -> str:
    """CPU Processor ID via WMIC — stable, hard to spoof."""
    try:
        out = subprocess.check_output(
            ["wmic", "cpu", "get", "ProcessorId"],
            creationflags=0x08000000,   # CREATE_NO_WINDOW
            timeout=5).decode(errors="ignore")
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        # lines[0] = header "ProcessorId", lines[1] = value
        return lines[1] if len(lines) > 1 else ""
    except Exception:
        return ""


def _disk_serial() -> str:
    """C: drive volume serial number."""
    try:
        out = subprocess.check_output(
            ["wmic", "logicaldisk", "where", "Caption='C:'",
             "get", "VolumeSerialNumber"],
            creationflags=0x08000000,
            timeout=5).decode(errors="ignore")
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        return lines[1] if len(lines) > 1 else ""
    except Exception:
        return ""


def get_hwid() -> str:
    """
    Returns a stable 64-char SHA-256 hex string for this machine.
    Falls back gracefully if any component is unavailable.
    """
    parts = [_machine_guid(), _cpu_id(), _disk_serial()]
    combined = "|".join(p.upper() for p in parts if p)
    if not combined:
        # Last resort: hostname hash (weak but better than nothing)
        import socket
        combined = socket.gethostname()
    return hashlib.sha256(combined.encode()).hexdigest()
