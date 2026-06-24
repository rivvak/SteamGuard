"""
SteamGuard — Universal Steam Sharing Bypass
============================================
Auto-detects the game you're playing, monitors your network connection,
and automatically re-applies the firewall block the instant your internet
reconnects — before Steam's servers can send the "Shared Library Locked"
kick signal.

HOW IT WORKS:
  Steam's library lock is enforced by Valve's Connection Manager (CM) servers
  over TCP port 443 (WebSocket). When your wifi reconnects, Steam re-contacts
  these servers within 1–5 seconds, which triggers the 2-minute kick countdown.

  This tool:
  1. Creates a targeted Windows Firewall rule blocking Steam.exe → Valve CM IPs
  2. Monitors your network for reconnect events (WMI + psutil dual-layer)
  3. Re-applies the block within ~200ms of detecting reconnection
  4. Auto-detects your running Steam game from the registry + ACF manifests
  5. Shows live status so you always know what's happening

  The block targets Valve's AS32590 IP ranges ONLY. Games using Epic Online
  Services (Police Simulator, Meccha Chameleon, etc.) connect to Epic/AWS
  servers — completely unaffected by this rule.

REQUIREMENTS:
  - Windows 10/11
  - Python 3.10+
  - Run as Administrator (required for firewall rule management)
  - pip install psutil wmi pywin32  (wmi + pywin32 are optional — fallback to psutil)
"""

import tkinter as tk
from tkinter import scrolledtext
from tkinter import ttk
import subprocess
import threading
import ctypes
import sys
import os
import re
import time
import json
import winreg
import glob
import socket
import urllib.request
import math
import struct
import winsound
import io
import base64
from datetime import datetime, timedelta
from ipaddress import ip_address, ip_network
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
import urllib.request
import webbrowser
import sys
import json as _json_mod
from steam_features import (
    WhyLockedDialog, SteamDeckHealthDialog, OfflineReadinessDialog,
    FamilyCooldownDialog, DLCAdvisorDialog, PreLaunchWarning,
    classify_game, GameBadgePanel,
    get_running_appid_reg, _reg_get,
)

CURRENT_VERSION = "1.4.0"

def check_for_updates():
    """Check GitHub for updates. Verifies SHA-256 of manifest if available."""
    try:
        url = "https://raw.githubusercontent.com/rivvak/SteamGuard/main/version.txt"
        with urllib.request.urlopen(url, timeout=4) as resp:
            latest_version = resp.read().decode("utf-8").strip()
        
        if latest_version != CURRENT_VERSION:
            # Try to fetch update manifest for integrity info
            manifest_url = "https://raw.githubusercontent.com/rivvak/SteamGuard/main/update_manifest.json"
            manifest = {}
            try:
                with urllib.request.urlopen(manifest_url, timeout=4) as r:
                    manifest = json.loads(r.read())
            except Exception:
                pass
            
            msg = (f"SteamGuard v{latest_version} is available!\n\n"
                   f"Current version: {CURRENT_VERSION}\n"
                   f"Latest version: {latest_version}\n\n"
                   f"Click OK to download the update.")
            if manifest.get("mandatory"):
                msg += "\n\n⚠ This is a mandatory security update."
            
            from tkinter import messagebox
            messagebox.showwarning("Update Required", msg)
            webbrowser.open(manifest.get("url", "https://github.com/rivvak/SteamGuard/releases/latest"))
            sys.exit(0)
    except SystemExit:
        sys.exit(0)
    except Exception:
        pass # Fallback gracefully if offline/network error

# ─────────────────────────────────────────────────────────────────────────────
# App data paths
# ─────────────────────────────────────────────────────────────────────────────

_APPDATA_DIR = Path(os.environ.get("APPDATA", "")) / "SteamGuard"
_CONFIG_FILE = _APPDATA_DIR / "config.json"
_LOG_FILE    = _APPDATA_DIR / "steamguard.log"

def _ensure_appdata_dir():
    _APPDATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Config load/save ──────────────────────────────────────────────────────────

_DEFAULT_CONFIG = {
    "auto_protect": True,
    "autostart":    False,
}

def load_config() -> dict:
    try:
        if _CONFIG_FILE.exists():
            return {**_DEFAULT_CONFIG, **json.loads(_CONFIG_FILE.read_text("utf-8"))}
    except Exception:
        pass
    return dict(_DEFAULT_CONFIG)

def save_config(cfg: dict):
    try:
        _ensure_appdata_dir()
        _CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass

# ── File logger ───────────────────────────────────────────────────────────────

_log_file_handle = None

def _open_log_file():
    global _log_file_handle
    try:
        _ensure_appdata_dir()
        _log_file_handle = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
        _log_file_handle.write(
            f"\n{'='*60}\n"
            f"SteamGuard session started {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"{'='*60}\n")
    except Exception:
        _log_file_handle = None

def log_to_file(msg: str):
    if _log_file_handle:
        try:
            _log_file_handle.write(f"[{datetime.now():%H:%M:%S}]  {msg}\n")
        except Exception:
            pass

def _close_log_file():
    global _log_file_handle
    if _log_file_handle:
        try:
            _log_file_handle.write(
                f"{'='*60}\n"
                f"Session ended {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                f"{'='*60}\n")
            _log_file_handle.close()
        except Exception:
            pass
        _log_file_handle = None

# ── Windows toast notification ────────────────────────────────────────────────

def _show_toast(title: str, msg: str):
    """
    Show a Windows balloon tip via the tray icon if available,
    otherwise fall back to a PowerShell BurntToast / Shell_NotifyIcon.
    Runs in a background thread — never blocks.
    """
    def _do():
        try:
            # Try Shell_NotifyIcon via PowerShell (works on Win10+ without extras)
            ps = (
                f"Add-Type -AssemblyName System.Windows.Forms; "
                f"$n = New-Object System.Windows.Forms.NotifyIcon; "
                f"$n.Icon = [System.Drawing.SystemIcons]::Shield; "
                f"$n.Visible = $true; "
                f"$n.ShowBalloonTip(3000, '{title}', '{msg}', "
                f"[System.Windows.Forms.ToolTipIcon]::Info); "
                f"Start-Sleep -Milliseconds 3500; "
                f"$n.Visible = $false; $n.Dispose()"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-WindowStyle", "Hidden", "-Command", ps],
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=6)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()

# ── Sound feedback ────────────────────────────────────────────────────────────

def _play_protect_sound():
    """Short ascending two-tone beep using winsound.Beep (no file needed)."""
    def _do():
        try:
            winsound.Beep(880, 80)
            winsound.Beep(1320, 120)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()

def _play_unprotect_sound():
    """Short descending tone."""
    def _do():
        try:
            winsound.Beep(660, 80)
            winsound.Beep(440, 120)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()

# ─────────────────────────────────────────────────────────────────────────────
# Valve AS32590 CM IP Ranges
# All Steam Connection Manager servers live in these CIDRs.
# Blocking Steam.exe to these IPs prevents the library lock signal.
# Game servers (EOS/Epic, game-specific) are on different IP ranges.
# ─────────────────────────────────────────────────────────────────────────────

VALVE_CIDRS = [
    "162.254.192.0/21",   # Primary US/EU CM (sea1, lax1, ord1, iad1, atl3, fra1)
    "155.133.224.0/19",   # Secondary CM + relay (all 155.133.x sub-ranges)
    "103.10.124.0/23",    # Singapore/AU CM — covers .124 AND .125 (confirmed live)
    "103.28.54.0/24",     # Hong Kong CM hkg1 — cmp*-hkg1.steamserver.net (confirmed live)
    "153.254.86.0/24",    # Hong Kong CM (legacy)
    "205.196.6.0/24",     # Seattle CM
    "208.64.200.0/21",    # Tukwila WA CDN + CM
    "205.185.194.0/23",   # São Paulo CM
    "146.66.152.0/24",    # Vienna EU CM
    "146.66.155.0/24",    # Vienna EU CM
    "45.121.184.0/23",    # Tokyo APAC CM (tyo3)
    "190.217.33.0/24",    # Lima CM
    "185.25.182.0/23",    # Paris/Dubai CM — covers .182 AND .183 (confirmed live)
]

# Pre-parsed network objects for fast IP matching
# Extended at runtime by refresh_cm_cidrs() with any newly discovered IPs.
_VALVE_NETS: list = [ip_network(cidr) for cidr in VALVE_CIDRS]

# Quick prefix match strings (faster than full CIDR check for the hot path)
VALVE_PREFIXES = [
    "162.254.19", "155.133.2", "103.10.124", "103.10.125",
    "103.28.54",  "153.254.86","205.196.6",  "208.64.20",
    "205.185.19", "146.66.1",  "45.121.18",  "190.217.33",
    "185.25.182", "185.25.183",
]

RULE_NAME  = "SteamGuard_LockBypass"
STEAM_EXES = [
    r"C:\Program Files (x86)\Steam\steam.exe",
    r"C:\Program Files\Steam\steam.exe",
]

# ─────────────────────────────────────────────────────────────────────────────
# Dynamic CM server list refresh
# ─────────────────────────────────────────────────────────────────────────────

def refresh_cm_cidrs() -> tuple[int, int]:
    """
    Query Valve's live CM server list and extend _VALVE_NETS with any IPs not
    already covered by the static VALVE_CIDRS.

    Tries the newer GetCMListForConnect endpoint first (returns structured
    server objects with type/datacenter info), then falls back to the older
    GetCMList endpoint (returns raw IP:port strings) which works without the
    structured response key.

    Returns: (new_ips_added, total_servers_seen)
    """
    global _VALVE_NETS

    # Try newest API first, then fall back to the older simpler one
    _ENDPOINTS = [
        ("https://api.steampowered.com/ISteamDirectory/"
         "GetCMListForConnect/v1/?cellid=0",   "serverlist"),
        ("https://api.steampowered.com/ISteamDirectory/"
         "GetCMList/v1/?cellid=0",             "serverlist"),
    ]

    live_ips: set[str] = set()
    total = 0

    for url, key in _ENDPOINTS:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "SteamGuard/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            servers = data.get("response", {}).get(key, [])
            if not servers:
                continue
            total = len(servers)
            for entry in servers:
                # GetCMListForConnect returns dict objects; GetCMList returns strings
                if isinstance(entry, dict):
                    ep   = entry.get("endpoint", "")
                    host = ep.rsplit(":", 1)[0] if ep else ""
                    kind = entry.get("type", "")
                else:
                    # Older format: "IP:port"
                    host = str(entry).rsplit(":", 1)[0]
                    kind = ""

                if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
                    live_ips.add(host)
                elif kind == "websocket" and host:
                    # Resolve hostname e.g. cmp1-hkg1.steamserver.net
                    try:
                        for res in socket.getaddrinfo(host, None, socket.AF_INET):
                            live_ips.add(res[4][0])
                    except Exception:
                        pass
            break  # success — no need to try next endpoint
        except Exception:
            continue  # try next endpoint

    new_count = 0
    for ip_str in live_ips:
        try:
            addr = ip_address(ip_str)
            if not any(addr in net for net in _VALVE_NETS):
                _VALVE_NETS.append(ip_network(f"{ip_str}/32"))
                new_count += 1
        except ValueError:
            pass

    return new_count, total

# ─────────────────────────────────────────────────────────────────────────────
# Palette
# ─────────────────────────────────────────────────────────────────────────────

# ── "Steam Vault" palette (from UI research) ──────────────────────────────────
BG_BASE     = "#0D1117"   # app background (deepest)
BG_SIDEBAR  = "#161B22"   # sidebar / nav rail
BG_CARD     = "#1C2128"   # cards / panels
BG_ELEVATED = "#21262D"   # hover / elevated surfaces
ACCENT      = "#23A559"   # primary accent — green (trust/secure)
ACCENT_CYAN = "#00E5FF"   # cyber accent for focus rings / secondary
ACCENT_BLUE = "#58A6FF"   # info / links
GREEN       = "#23A559"   # secure / verified
RED         = "#F23F43"
YELLOW      = "#F0B232"
PURPLE      = "#BC8CFF"
TEXT_MAIN   = "#E6EDF3"
TEXT_DIM    = "#8B949E"
BORDER      = "#30363D"

# Backward-compat aliases (older code paths reference these names)
BG_DARK   = BG_BASE
BG_MID    = BG_SIDEBAR
BG_PANEL  = BG_CARD

F_HEAD   = ("Segoe UI", 14, "bold")    # section headings
F_BODY   = ("Segoe UI", 11)            # body text
F_SMALL  = ("Segoe UI", 10)            # small text
F_MONO   = ("Consolas", 10)            # codes / logs
F_TITLE  = ("Segoe UI", 16, "bold")    # app title
F_LABEL  = ("Segoe UI", 9)             # dim section labels / caps

# ─────────────────────────────────────────────────────────────────────────────
# DPAPI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _dpapi_protect(data: bytes) -> bytes:
    """Encrypt bytes using Windows DPAPI (user-scope). Falls back to plaintext."""
    try:
        import win32crypt
        return win32crypt.CryptProtectData(data, "SteamGuard", None, None, None, 0)
    except Exception:
        return data  # graceful fallback — dev mode / missing pywin32

def _dpapi_unprotect(blob: bytes) -> bytes:
    """Decrypt bytes using Windows DPAPI. Falls back to plaintext."""
    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
    except Exception:
        return blob

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def elevate():
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{os.path.abspath(__file__)}"', None, 1)
    if result > 32:  # ShellExecute returns >32 on success
        sys.exit(0)
    # UAC was cancelled or failed — stay open so the user can see the warning

def is_valve_ip(ip_str: str) -> bool:
    """Fast check — prefix match first, then full CIDR."""
    if any(ip_str.startswith(p) for p in VALVE_PREFIXES):
        return True
    try:
        addr = ip_address(ip_str)
        return any(addr in net for net in _VALVE_NETS)
    except ValueError:
        return False

def has_internet(host="8.8.8.8", port=53, timeout=1.5) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)  # per-socket timeout — never touch the global default
        try:
            sock.connect((host, port))
            return True
        finally:
            sock.close()
    except OSError:
        return False

# ─────────────────────────────────────────────────────────────────────────────
# Steam / Game Detection
# ─────────────────────────────────────────────────────────────────────────────

def get_steam_exe() -> str | None:
    for p in STEAM_EXES:
        if os.path.exists(p):
            return p
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\WOW6432Node\Valve\Steam") as key:
            path, _ = winreg.QueryValueEx(key, "InstallPath")
        exe = os.path.join(path, "steam.exe")
        return exe if os.path.exists(exe) else None
    except Exception:
        return None

def get_steam_install_dir() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam") as key:
            path, _ = winreg.QueryValueEx(key, "SteamPath")
        return path.replace("/", "\\")
    except Exception:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\WOW6432Node\Valve\Steam") as key:
                path, _ = winreg.QueryValueEx(key, "InstallPath")
            return path
        except Exception:
            return None

def get_library_paths(steam_dir: str) -> list[str]:
    paths = [os.path.join(steam_dir, "steamapps")]
    vdf = os.path.join(steam_dir, "steamapps", "libraryfolders.vdf")
    if os.path.exists(vdf):
        try:
            with open(vdf, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    m = re.search(r'"path"\s+"([^"]+)"', line)
                    if m:
                        lib = m.group(1).replace("\\\\", "\\").replace("/", "\\")
                        paths.append(os.path.join(lib, "steamapps"))
        except Exception:
            pass
    return [p for p in paths if os.path.isdir(p)]

def parse_acf(path: str) -> dict:
    result = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for key in ("appid", "name", "installdir"):
            m = re.search(rf'"{key}"\s+"([^"]+)"', content, re.IGNORECASE)
            if m:
                result[key] = m.group(1)
    except Exception:
        pass
    return result

def build_game_catalog(steam_dir: str) -> dict[int, dict]:
    """Returns {appid: {name, installdir, exe_candidates}} for all installed games."""
    catalog = {}
    for lib in get_library_paths(steam_dir):
        for acf_path in glob.glob(os.path.join(lib, "appmanifest_*.acf")):
            data = parse_acf(acf_path)
            if "appid" not in data or "name" not in data:
                continue
            appid    = int(data["appid"])
            game_dir = os.path.join(lib, "common", data.get("installdir", ""))
            # Find executable candidates in game dir (top-level .exe files)
            exes = []
            if os.path.isdir(game_dir):
                try:
                    exes = [f.lower() for f in os.listdir(game_dir)
                            if f.lower().endswith(".exe")]
                except Exception:
                    pass
            catalog[appid] = {
                "name":     data["name"],
                "game_dir": game_dir,
                "exes":     exes,
                "lib":      lib,
            }
    return catalog

def get_running_appid_registry() -> int | None:
    """Read Steam's RunningAppID registry key — most reliable single-call method."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam") as key:
            appid, _ = winreg.QueryValueEx(key, "RunningAppID")
        return int(appid) if appid and int(appid) != 0 else None
    except Exception:
        return None

def get_running_appid_apps_key() -> int | None:
    r"""Scan HKCU\SOFTWARE\Valve\Steam\Apps\<id>\Running == 1."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"SOFTWARE\Valve\Steam\Apps") as apps_key:
            i = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(apps_key, i)
                    with winreg.OpenKey(apps_key, sub_name) as sub:
                        try:
                            running, _ = winreg.QueryValueEx(sub, "Running")
                            if running == 1:
                                return int(sub_name)
                        except Exception:
                            pass
                    i += 1
                except OSError:
                    break
    except Exception:
        pass
    return None

def get_running_game(catalog: dict[int, dict]) -> dict | None:
    """Detect the currently running Steam game. Returns game info dict or None."""
    # Method 1: registry RunningAppID (fastest)
    appid = get_running_appid_registry()
    if appid and appid in catalog:
        return {"appid": appid, **catalog[appid], "method": "registry"}

    # Method 2: scan Apps\<id>\Running key
    appid = get_running_appid_apps_key()
    if appid and appid in catalog:
        return {"appid": appid, **catalog[appid], "method": "apps_key"}

    # Method 3: scan running processes against catalog exe names
    try:
        import psutil
        running_exes = {p.name().lower() for p in psutil.process_iter(["name"])
                        if p.info.get("name")}
        for appid, info in catalog.items():
            for exe in info["exes"]:
                if exe in running_exes:
                    return {"appid": appid, **info, "method": "process_match",
                            "matched_exe": exe}
    except Exception:
        pass

    return None

# ─────────────────────────────────────────────────────────────────────────────
# Firewall Controller
# ─────────────────────────────────────────────────────────────────────────────

def run_ps(cmd: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW)
    return r.returncode, r.stdout.strip(), r.stderr.strip()

def run_netsh(args: str) -> int:
    """Use netsh for sub-50ms rule toggles (faster than PowerShell startup)."""
    r = subprocess.run(
        f"netsh advfirewall firewall {args}",
        shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    return r.returncode

def fw_rule_state() -> bool | None:
    """None = no rule, True = enabled (blocking), False = disabled."""
    rc, out, _ = run_ps(
        f"(Get-NetFirewallRule -DisplayName '{RULE_NAME}' "
        f"-ErrorAction SilentlyContinue).Enabled")
    if rc != 0 or not out.strip():
        return None
    return out.strip().lower() == "true"

def _validate_cidrs_ps(cidrs: list[str]) -> tuple[list[str], list[str]]:
    """
    Validate each CIDR against Windows Firewall in a single PowerShell call.
    Returns (valid_cidrs, invalid_cidrs).

    Key details:
    - PS array requires commas between elements: @('a','b') not @('a' 'b')
    - Cleans up any leftover test rule before starting
    - Falls back to returning all as valid if the script itself can't run
    """
    # Comma-separated quoted elements — required for PS array literal syntax
    cidr_args = ",".join(f"'{c}'" for c in cidrs)
    script = (
        # Clean up any leftover test rule from a previous crashed run
        "Remove-NetFirewallRule -DisplayName '_SG_T' -ErrorAction SilentlyContinue | Out-Null; "
        f"$cidrs = @({cidr_args}); "
        "$out = @(); "
        "foreach ($c in $cidrs) { "
        "  try { "
        "    $null = New-NetFirewallRule -DisplayName '_SG_T' "
        "      -Direction Outbound -RemoteAddress $c "
        "      -Action Block -Enabled False -Profile Any -ErrorAction Stop; "
        "    Remove-NetFirewallRule -DisplayName '_SG_T' "
        "      -ErrorAction SilentlyContinue | Out-Null; "
        "    $out += 'OK:' + $c "
        "  } catch { "
        "    $out += 'FAIL:' + $c "
        "  } "
        "}; "
        "$out -join '|'"
    )
    rc, out, _ = run_ps(script)
    valid, invalid = [], []
    if rc == 0 and out.strip():
        for token in out.strip().split("|"):
            token = token.strip()
            if token.startswith("OK:"):
                valid.append(token[3:])
            elif token.startswith("FAIL:"):
                invalid.append(token[5:])
    if not valid and not invalid:
        # Script produced no output or failed — assume all valid, let fw_create decide
        valid = list(cidrs)
    return valid, invalid

def fw_create(steam_exe: str) -> tuple[bool, str]:
    """
    Create the SteamGuard firewall rule — full outbound block to Valve CM IPs.

    WHY FULL BLOCK (not TCP-only):
      Steam's SDR relay (used for multiplayer) initialises its relay token
      through the same CM TCP connection.  If CM is reachable even via UDP,
      Steam will still connect, get a valid session, and the library-lock
      signal will eventually arrive.  Every confirmed working implementation
      (SSM, SteamLibraryUnblocker, SteamSharedLibraryTweaks) uses a full
      protocol block for this reason.

      Correct workflow: you launch the game FIRST (game connects to its own
      servers before the block fires), THEN enable protection, THEN your
      friend launches on his end.  With the block active your Steam can't
      tell Valve you're playing → no lock signal is ever sent.

    -RemoteAddress must NOT be quoted as one string — PowerShell must see
    comma-separated tokens so it coerces them to string[] for the WFP API.
    """
    def _make_cmd(exe: str, cidrs: list[str]) -> str:
        return (
            f"New-NetFirewallRule "
            f"-DisplayName '{RULE_NAME}' "
            f"-Direction Outbound "
            f"-Program '{exe}' "
            f"-RemoteAddress {','.join(cidrs)} "
            f"-Action Block -Profile Any "
            f"-Enabled False"
        )

    # Attempt 1: all CIDRs at once (fast path)
    rc, _, err = run_ps(_make_cmd(steam_exe, VALVE_CIDRS))
    if rc == 0:
        return True, "OK"

    # Attempt 2: validate each CIDR individually, retry with valid subset
    valid, invalid = _validate_cidrs_ps(VALVE_CIDRS)
    if invalid:
        note = f"Skipped {len(invalid)} incompatible CIDR(s): {invalid}"
    else:
        note = err
    if not valid:
        return False, f"No valid CIDRs — {err}"
    rc2, _, err2 = run_ps(_make_cmd(steam_exe, valid))
    return rc2 == 0, (note if rc2 == 0 else (err2 or err))

def _kill_valve_connections(sleep_after: bool = True) -> int:
    """
    After the firewall rule is enabled, existing TCP connections to Valve CM
    servers remain ESTABLISHED — Windows Firewall only blocks NEW connections.
    Those live connections can still deliver the library-lock signal.

    Uses SetTcpEntry (iphlpapi) to force each Valve-bound TCP connection into
    DELETE_TCB state, which immediately sends a RST and closes the socket.
    This is the same technique used by tools like CurrPorts and TCPView.

    Returns the number of connections that were reset.
    """
    import ctypes
    import ctypes.wintypes
    import struct

    # MIB_TCPROW structure for SetTcpEntry
    # dwState, dwLocalAddr, dwLocalPort, dwRemoteAddr, dwRemotePort
    class MIB_TCPROW(ctypes.Structure):
        _fields_ = [
            ("dwState",      ctypes.c_ulong),
            ("dwLocalAddr",  ctypes.c_ulong),
            ("dwLocalPort",  ctypes.c_ulong),
            ("dwRemoteAddr", ctypes.c_ulong),
            ("dwRemotePort", ctypes.c_ulong),
        ]

    MIB_TCP_STATE_DELETE_TCB = 12

    def ip_to_ulong(ip_str: str) -> int:
        try:
            packed = socket.inet_aton(ip_str)
            return struct.unpack("<I", packed)[0]
        except Exception:
            return 0

    def port_to_net(port: int) -> int:
        # SetTcpEntry expects port in network byte order packed into a ulong
        return socket.htons(port)

    killed = 0
    try:
        iphlp = ctypes.windll.iphlpapi

        # Get all TCP connections via GetExtendedTcpTable
        # Use psutil as the fast path to enumerate connections
        try:
            import psutil
            all_conns = psutil.net_connections(kind="tcp4")
        except Exception:
            return 0

        for c in all_conns:
            if c.status != "ESTABLISHED" or not c.raddr:
                continue
            rip = c.raddr.ip
            if not is_valve_ip(rip):
                continue

            row = MIB_TCPROW()
            row.dwState      = MIB_TCP_STATE_DELETE_TCB
            row.dwLocalAddr  = ip_to_ulong(c.laddr.ip)
            row.dwLocalPort  = port_to_net(c.laddr.port)
            row.dwRemoteAddr = ip_to_ulong(rip)
            row.dwRemotePort = port_to_net(c.raddr.port)

            rc = iphlp.SetTcpEntry(ctypes.byref(row))
            if rc == 0:  # NO_ERROR
                killed += 1

    except Exception:
        pass

    if killed and sleep_after:
        time.sleep(1.0)  # let Steam detect the RST and go offline
    return killed

def fw_enable_fast() -> bool:
    """Enable via netsh — sub-50ms, no PowerShell startup overhead."""
    rc = run_netsh(f'set rule name="{RULE_NAME}" new enable=yes')
    return rc == 0

def fw_disable_fast() -> bool:
    rc = run_netsh(f'set rule name="{RULE_NAME}" new enable=no')
    return rc == 0

def fw_remove() -> bool:
    rc = run_netsh(f'delete rule name="{RULE_NAME}"')
    return rc == 0

# ─────────────────────────────────────────────────────────────────────────────
# Network Monitors
# ─────────────────────────────────────────────────────────────────────────────

class NetworkMonitor:
    """
    Multi-layer network reconnect detector.

    Layer 0: NotifyNetworkConnectivityHintChange — Windows kernel callback,
             <10 ms latency. Win10 2004 (build 19041)+ only; silently skipped
             on older systems.
    Layer 1: psutil polls Steam.exe TCP connections every 100–500 ms.
    Layer 2: WMI adapter change watch (~1 s after interface-up event).
    Layer 3: internet connectivity poll every 2 s (fallback, uses
             GetNetworkConnectivityHint fast-path on Win10 2004+).

    All layers call on_valve_connection_detected() when a reconnect or CM
    IP is detected.
    """

    def __init__(self, on_detected_callback):
        self._callback   = on_detected_callback
        self._running    = False
        self._steam_proc = None
        self._known_cm   = set()  # already-known CM IPs
        self._active     = True   # whether to monitor (pause when rule is active)
        # Kept alive so the kernel callback isn't GC'd while running
        self._nlm_cb     = None
        self._nlm_handle = None

    def start(self):
        self._running = True
        threading.Thread(target=self._connectivity_hint_loop, daemon=True).start()
        threading.Thread(target=self._psutil_loop,            daemon=True).start()
        threading.Thread(target=self._wmi_loop,               daemon=True).start()
        threading.Thread(target=self._internet_loop,          daemon=True).start()

    def stop(self):
        self._running = False

    def set_active(self, active: bool):
        """When active=True we're watching for CM reconnects. False = standby."""
        self._active = active
        if active:
            self._known_cm.clear()  # reset so next CM connection triggers callback

    def _connectivity_hint_loop(self):
        """
        Layer 0 — Windows kernel connectivity hint (Win10 build 19041+).

        NotifyNetworkConnectivityHintChange registers a kernel callback that
        fires within ~10 ms of any network state change, far faster than any
        polling approach.  Silently exits on older Windows; the other layers
        then provide full coverage.

        NetworkConnectivityLevelHint values:
          0 Unknown  1 None  2 Hidden  3 LocalAccess
          4 InternetAccess  5 ConstrainedInternetAccess
        """
        try:
            from ctypes import CFUNCTYPE, POINTER, c_void_p, c_uint32, byref, HANDLE
            _ip = ctypes.WinDLL("iphlpapi.dll", use_last_error=True)

            class _Hint(ctypes.Structure):
                _fields_ = [("level", c_uint32), ("cost", c_uint32)]

            _prev_level = [0]
            _CB = CFUNCTYPE(None, c_void_p, POINTER(_Hint))

            def _on_change(ctx, ptr):
                lvl = ptr.contents.level if ptr else 0
                prev = _prev_level[0]
                _prev_level[0] = lvl
                # Transition into InternetAccess (4) = reconnect event
                if prev < 4 <= lvl and self._active:
                    self._callback("connectivity_hint",
                                   f"internet level {prev}→{lvl}")

            self._nlm_cb = _CB(_on_change)   # keep alive — GC would break callback
            h = HANDLE()
            rc = _ip.NotifyNetworkConnectivityHintChange(
                self._nlm_cb, None, True, byref(h))
            if rc != 0:
                return  # API unavailable on this Windows version
            self._nlm_handle = h
            # Park here; callbacks fire automatically on a Windows thread-pool thread
            while self._running:
                time.sleep(1)
            try:
                _ip.CancelMibChangeNotify2(h)
            except Exception:
                pass
        except Exception:
            pass  # Win10 2004+ only — layers 1–3 provide coverage on older Windows

    def _find_steam_proc(self):
        try:
            import psutil
            for p in psutil.process_iter(["name"]):
                if p.info.get("name", "").lower() == "steam.exe":
                    return p
        except Exception:
            pass
        return None

    def _psutil_loop(self):
        try:
            import psutil
        except ImportError:
            return  # psutil not installed, skip this layer

        interval_normal  = 0.5   # when rule is active
        interval_alert   = 0.10  # when internet just came back
        _alert_until     = 0

        while self._running:
            now = time.time()
            interval = interval_alert if now < _alert_until else interval_normal

            # Only bother finding Steam proc when we're actively watching
            if self._active and self._steam_proc is None:
                self._steam_proc = self._find_steam_proc()
            elif not self._active:
                self._steam_proc = None  # release reference when idle

            if self._steam_proc and self._active:
                try:
                    if not self._steam_proc.is_running():
                        self._steam_proc = None
                        time.sleep(interval)
                        continue
                    # net_connections() added in psutil 5.9.0; fall back for older installs
                    try:
                        conns = self._steam_proc.net_connections(kind="tcp4")
                    except AttributeError:
                        conns = self._steam_proc.connections(kind="tcp4")
                    for c in conns:
                        if c.status == "ESTABLISHED" and c.raddr:
                            ip = c.raddr.ip
                            if ip not in self._known_cm and is_valve_ip(ip):
                                self._known_cm.add(ip)
                                self._callback("psutil", ip)
                                _alert_until = time.time() + 10  # stay alert
                except Exception:
                    self._steam_proc = None

            time.sleep(interval)

    def _wmi_loop(self):
        """WMI network adapter watch — fires ~1s after interface state change."""
        try:
            import wmi as _wmi
            c = _wmi.WMI()
            watcher = c.Win32_NetworkAdapterConfiguration.watch_for(
                "modification", delay_secs=1)
            while self._running:
                try:
                    event = watcher(timeout_ms=2000)
                    if event and event.IPEnabled and event.IPAddress:
                        if self._active:
                            self._callback("wmi_adapter", event.IPAddress[0])
                except Exception:
                    time.sleep(1)
        except ImportError:
            pass  # wmi not installed
        except Exception:
            pass

    @staticmethod
    def _connectivity_level() -> int:
        """
        Return the Windows network connectivity level (0-5) using
        GetNetworkConnectivityHint (Win10 2004+, no network round-trip needed)
        or fall back to a TCP dial to 8.8.8.8:53.

        Level 4 = InternetAccess (the threshold we consider "online").
        """
        try:
            class _H(ctypes.Structure):
                _fields_ = [("level", ctypes.c_uint32),
                             ("cost",  ctypes.c_uint32)]
            h = _H()
            if ctypes.windll.iphlpapi.GetNetworkConnectivityHint(
                    ctypes.byref(h)) == 0:
                return h.level
        except Exception:
            pass
        return 4 if has_internet() else 0  # fallback: socket dial

    def _internet_loop(self):
        """
        Layer 3 — fallback connectivity poll every 2 s.

        Uses GetNetworkConnectivityHint (kernel, no outbound packet) when
        available on Win10 2004+; falls back to a TCP dial on older Windows.
        Layer 0 (connectivity_hint_loop) fires much faster when available, so
        this loop acts as a safety net for systems where layer 0 is not active.
        """
        was_level = self._connectivity_level()
        while self._running:
            time.sleep(2)
            now_level = self._connectivity_level()
            if was_level < 4 <= now_level and self._active:
                self._callback("internet_poll", "reconnected")
            was_level = now_level

# ─────────────────────────────────────────────────────────────────────────────
# Windows autostart helpers
# ─────────────────────────────────────────────────────────────────────────────

_AUTOSTART_KEY  = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_NAME = "SteamGuard"

def get_autostart() -> bool:
    """Return True if SteamGuard is registered to start with Windows."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY) as k:
            winreg.QueryValueEx(k, _AUTOSTART_NAME)
            return True
    except Exception:
        return False

def set_autostart(enabled: bool) -> bool:
    """Add or remove SteamGuard from the Windows Run registry key."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY,
                            0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
                winreg.SetValueEx(k, _AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, _AUTOSTART_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False

# ─────────────────────────────────────────────────────────────────────────────
# Tooltip helper
# ─────────────────────────────────────────────────────────────────────────────

class Tooltip:
    def __init__(self, widget, text, delay=600):
        self._widget = widget
        self._text = text
        self._delay = delay
        self._tip_win = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._cancel)
        widget.bind("<ButtonPress>", self._cancel)

    def _schedule(self, event=None):
        self._cancel()
        self._after_id = self._widget.after(self._delay, self._show)

    def _cancel(self, event=None):
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip_win:
            self._tip_win.destroy()
            self._tip_win = None

    def _show(self):
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip_win = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self._text, bg="#1c2128", fg="#e6edf3",
                 font=("Segoe UI", 8), relief="flat", bd=0,
                 padx=8, pady=4).pack()
        tw.after(3000, self._cancel)

# ─────────────────────────────────────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────────────────────────────────────

class SteamGuard(tk.Tk):

    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        self.title("SteamGuard")

        _open_log_file()
        self._cfg = load_config()

        self._admin          = is_admin()
        self._steam_exe      = get_steam_exe()
        self._steam_dir      = get_steam_install_dir()
        self._catalog        = {}
        self._running_game: dict | None = None
        self._running_appid: int | None = None   # separate from dict for fast compare
        self._fw_state       = None   # None / True / False
        self._auto_heal      = tk.BooleanVar(value=self._cfg.get("auto_protect", True))
        self._autostart_var  = tk.BooleanVar(value=get_autostart())
        self._protected      = False  # are we actively protecting?
        self._protect_start: datetime | None = None  # when protection was activated
        self._heal_count     = 0      # network reconnect heals
        self._rule_heal_cnt  = 0      # rule self-heals (recreate/re-enable)
        self._last_heal_time: datetime | None = None
        self._session_start  = datetime.now()
        self._app_running    = True   # cleared in _on_close to stop bg threads
        self._protection_busy = False  # True while rule create/enable is in flight

        # New instance vars
        self._drag_x = 0
        self._drag_y = 0
        self._kill_counter = 0
        self._hourly_heals = [0] * 7
        self._game_art_photo = None
        self._rpc = None
        self._session_history_file = _APPDATA_DIR / "session_history.json"
        self._spark_ids = []

        # Heartbeat system vars
        self._heartbeat_session_id = ""
        self._last_heartbeat_ok = True

        # Badge/achievement system
        self._badges: list = []

        # ── New feature state (v1.4) ──────────────────────────────────────
        # Feature A: Playtime tracker pill widget refs
        self._pill_session_val = None
        self._pill_protected_val = None
        self._pill_heals_val = None
        self._after_pills_id = None
        # Feature B: Library locked status detector
        self._library_status = "unknown"   # "locked" / "free" / "unknown"
        self._lib_dot_cv = None
        self._lib_dot = None
        self._lib_lbl = None
        self._after_library_id = None
        # Feature C: Session playtime countdown (inside game card)
        self._game_detected_at: datetime | None = None
        self._playing_lbl = None
        self._after_playing_id = None
        # Feature D: Family lock quick-lock
        self._family_lock_active = False
        self._family_lock_after_id = None
        self._family_lock_btn = None

        # Trace auto-protect changes → persist
        self._auto_heal.trace_add("write", lambda *_: self._save_settings())

        self._monitor = NetworkMonitor(self._on_cm_detected)
        self._pre_launch = PreLaunchWarning(self._steam_dir, self._on_pre_launch_warning)

        # Animation state
        self._pulse_rings: list[dict] = []   # active expanding rings
        self._shield_glow  = 0.0             # 0.0–1.0 glow intensity
        self._shield_dir   = 1               # +1 brightening / -1 dimming
        self._anim_running = False

        self._build_ui()
        self._initial_load()

        self.update_idletasks()
        w, h = 520, 800
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tray_icon = None
        self._minimized_to_tray = False
        self._try_start_tray()

        # Hourly heal rotation
        self.after(3600000, self._rotate_hourly_heals)

    def _save_settings(self):
        cfg = {
            "auto_protect": self._auto_heal.get(),
            "autostart":    self._autostart_var.get(),
        }
        save_config(cfg)

    # ── Pre-launch warning callback ───────────────────────────────────────────

    def _on_pre_launch_warning(self, appid: int, game_name: str, locked_by: str):
        """Called by PreLaunchWarning when a new game launches while library is locked."""
        self.after(0, lambda: messagebox.showwarning(
            "SteamGuard — Launch Warning",
            f"⚠ {locked_by} is currently using your shared library.\n"
            f"Launching will kick them out of {game_name}.\n\n"
            f"Consider waiting until they finish, or start protection first."
        ))


    # ── Minimize (overrideredirect-safe) ──────────────────────────────

    def _minimize(self):
        """Minimize to taskbar safely when overrideredirect=True.

        Strategy: save position, withdraw the window (hides it but keeps
        it in the taskbar via a hidden helper Toplevel), then iconify
        the helper. On restore we deiconify, re-show at saved position.
        """
        # Save current geometry so we can restore exact position
        self._saved_geometry = self.geometry()
        # Turn off overrideredirect so Windows shows it in the taskbar
        self.overrideredirect(False)
        self.iconify()
        # Poll until the window is restored (wm_state == 'normal')
        self.after(200, self._check_restore)

    def _check_restore(self):
        state = self.wm_state()
        if state == "iconic" or state == "withdrawn":
            # Still minimized — keep polling
            self.after(200, self._check_restore)
        else:
            # Window has been restored by the user
            self.overrideredirect(True)
            if hasattr(self, "_saved_geometry"):
                self.geometry(self._saved_geometry)
            self.lift()
            self.focus_force()

    # ── Hover helpers ─────────────────────────────────────────────────────────

    def _hover_enter(self, btn, color_in): btn.config(bg=color_in)
    def _hover_leave(self, btn, color_out): btn.config(bg=color_out)

    # ── Hourly heal rotation ──────────────────────────────────────────────────

    def _rotate_hourly_heals(self):
        self._hourly_heals = self._hourly_heals[1:] + [0]
        self._redraw_chart()
        self.after(3600000, self._rotate_hourly_heals)

    # ── Kill counter ──────────────────────────────────────────────────────────

    def _increment_kill_counter(self, n: int):
        self._kill_counter += n
        self._kill_lbl.config(text=f"⚡ Connections severed: {self._kill_counter}", fg=YELLOW)
        self.after(400, lambda: self._kill_lbl.config(fg=TEXT_DIM))

    # ── Initial load ──────────────────────────────────────────────────────────

    def _initial_load(self):
        def worker():
            # ── Refresh CM server list from Valve's live API ───────────────
            self.after(0, lambda: self._log(
                "Fetching live CM server list from Valve API…"))
            new_ips, total = refresh_cm_cidrs()
            base = len(VALVE_CIDRS)
            if total:
                extra = f", +{new_ips} new IPs discovered" if new_ips else ""
                msg = (f"CM list: {total} live servers fetched"
                       f" ({base} static CIDRs{extra}).")
                self.after(0, lambda m=msg: self._log(m))
            else:
                self.after(0, lambda: self._log(
                    f"CM API unreachable — using {base} built-in CIDRs."))

            # ── Scan installed games ───────────────────────────────────────
            if self._steam_dir:
                self.after(0, lambda: self._log("Scanning Steam library…"))
                self._catalog = build_game_catalog(self._steam_dir)
                count = len(self._catalog)
                self.after(0, lambda: self._log(f"Found {count} installed games."))

            # ── Firewall rule setup ────────────────────────────────────────
            # Always recreate — ensures we have a clean rule with no stale
            # protocol filter from a previous version.
            state = fw_rule_state()
            if state is True:
                # Rule was enabled from a previous/crashed session — sync UI
                # so user sees the correct state without having to re-click.
                self.after(0, lambda: self._log(
                    "Firewall rule was already ACTIVE (previous session). "
                    "Resuming protection — Steam CM traffic is blocked."))
                self.after(0, lambda: self._sync_fw_state(True))
                self._protected = True
                self._protect_start = datetime.now()
                self.after(0, self._update_protect_btn)
                self.after(0, self._update_status_banner)
            elif state is not None:
                fw_remove()
                self.after(0, lambda: self._log(
                    "Existing rule removed — recreating fresh (full block)."))
                state = None
                self.after(0, lambda: self._sync_fw_state(state))
                if self._steam_exe:
                    ok, msg = fw_create(self._steam_exe)
                    if ok:
                        self.after(0, lambda: self._log(
                            "Firewall rule created (inactive). Ready — launch your "
                            "game, click START PROTECTION, then friend joins."))
                        self.after(0, lambda: self._sync_fw_state(False))
                    else:
                        self.after(0, lambda: self._log(
                            f"Could not create rule: {msg}", True))
            else:
                self.after(0, lambda: self._sync_fw_state(state))
                if self._steam_exe:
                    ok, msg = fw_create(self._steam_exe)
                    if ok:
                        self.after(0, lambda: self._log(
                            "Firewall rule created (inactive). Ready — launch your "
                            "game, click START PROTECTION, then friend joins."))
                        self.after(0, lambda: self._sync_fw_state(False))
                    else:
                        self.after(0, lambda: self._log(
                            f"Could not create rule: {msg}", True))

            self._monitor.start()
            self._pre_launch.start()
            self.after(0, lambda: self._log("Network monitor active (4 layers)."))
            # Start game detection loop
            self.after(2000, self._detect_game_loop)
            # Start rule self-heal monitor
            threading.Thread(target=self._rule_health_loop, daemon=True).start()
            # Start connection kill loop — proactively RSTs any Valve TCP
            # connections that form while protection is active
            threading.Thread(target=self._connection_kill_loop, daemon=True).start()

            # Start heartbeat loop
            threading.Thread(target=self._heartbeat_loop, daemon=True).start()

            # Discord rich presence (best-effort)
            def _start_rich_presence():
                try:
                    from pypresence import Presence
                    RPC = Presence("1234567890123456789")  # placeholder app ID
                    RPC.connect()
                    self._rpc = RPC
                    game_name = self._running_game["name"] if self._running_game else "Idle"
                    RPC.update(
                        state=f"Playing {game_name}",
                        details="Protected via SteamGuard",
                        large_image="shield",
                        start=int(self._session_start.timestamp())
                    )
                except Exception:
                    pass  # pypresence not installed or Discord not running

            threading.Thread(target=_start_rich_presence, daemon=True).start()

        threading.Thread(target=worker, daemon=True).start()

    # ── Heartbeat System ──────────────────────────────────────────────────────

    def _heartbeat_loop(self):
        import time as _time
        _time.sleep(30)  # initial delay
        while self._app_running:
            try:
                self._do_heartbeat()
            except Exception:
                pass
            _time.sleep(300)  # every 5 minutes

    def _do_heartbeat(self):
        # Load key and discord_user_id from config
        cfg_path = _APPDATA_DIR / "config.json"
        if not cfg_path.exists():
            return
        cfg = json.loads(cfg_path.read_text("utf-8"))
        key = cfg.get("license_key", "")
        discord_id = cfg.get("discord_user_id", "")
        if not key or not discord_id:
            return

        # Build heartbeat payload
        import hashlib, hmac as _hmac
        HMAC_SECRET = "7e3b9ccf02a09ad3520ebc7ed3f00a48d5eff34ef081900ee9064dba2a74529e"
        try:
            from auth.hwid import get_hwid
            hwid = get_hwid()
        except Exception:
            hwid = "unknown"

        sig = _hmac.new(HMAC_SECRET.encode(), f"{key}:{hwid}".encode(), hashlib.sha256).hexdigest()

        payload = json.dumps({
            "key": key,
            "hwid": hwid,
            "discord_user_id": discord_id,
            "sig": sig,
            "client_version": CURRENT_VERSION,
            "session_id": self._heartbeat_session_id,
            "protected": self._protected,
            "heal_count": self._heal_count + self._rule_heal_cnt,
            "kill_count": self._kill_counter,
            "game_appid": self._running_appid or 0,
            "game_name": self._running_game["name"] if self._running_game else "",
        }).encode()

        SERVER_URL = "https://steamguard-775181381055.us-central1.run.app"
        req = urllib.request.Request(
            SERVER_URL + "/heartbeat",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": f"SteamGuard/{CURRENT_VERSION}"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = json.loads(r.read())
        except Exception:
            self._last_heartbeat_ok = False
            # Update heartbeat status label
            status_text = "● Server sync: offline"
            status_color = YELLOW
            self.after(0, lambda t=status_text, c=status_color:
                self._heartbeat_status_lbl.config(text=t, fg=c))
            return

        self._last_heartbeat_ok = True
        if resp.get("session_id"):
            self._heartbeat_session_id = resp["session_id"]

        # Update heartbeat status label
        status_text = "● Server sync: OK"
        status_color = GREEN
        self.after(0, lambda t=status_text, c=status_color:
            self._heartbeat_status_lbl.config(text=t, fg=c))

        # Handle kill signal
        if resp.get("kill"):
            reason = resp.get("kill_reason", "License revoked")
            self.after(0, lambda r=reason: self._on_remote_kill(r))
            return

        # Handle badge awards
        new_badges = resp.get("new_badges", [])
        if new_badges:
            self.after(0, lambda b=new_badges: self._on_new_badges(b))

        # Update feature flags
        policy = resp.get("client_policy", {})
        if policy.get("mandatory_update"):
            self.after(0, lambda: self._log("A mandatory update is available. Please update SteamGuard.", level="warn"))

    def _on_remote_kill(self, reason: str):
        # Stop protection
        if self._protected:
            self._stop_protection()
        # Show message
        from tkinter import messagebox
        self._log(f"Remote kill received: {reason}", error=True)
        messagebox.showerror("SteamGuard — Access Revoked",
            f"Your license has been deactivated:\n\n{reason}\n\nContact support in Discord.")

    # ── Badge / Achievement System ────────────────────────────────────────────

    def _on_new_badges(self, badges: list):
        for badge in badges:
            if badge not in self._badges:
                self._badges.append(badge)
                self._log(f"🏆 Achievement unlocked: {badge}!", level="success", color=YELLOW)
        self._redraw_badges()

    def _redraw_badges(self):
        """Stats tab removed — no-op."""
        pass

    def _export_share_card(self):
        """Generate and save a share card PNG using Pillow."""
        try:
            from PIL import Image, ImageDraw, ImageFont
            import io, os

            W, H = 600, 280
            img = Image.new("RGB", (W, H), color=(13, 17, 23))  # BG_BASE #0D1117
            d = ImageDraw.Draw(img)

            # Background gradient effect (simple horizontal bands)
            for y in range(H):
                alpha = y / H
                r = int(13 + alpha * 15)
                g = int(17 + alpha * 20)
                b = int(23 + alpha * 30)
                d.line([(0, y), (W, y)], fill=(r, g, b))

            # Shield icon area (left panel)
            d.rectangle([0, 0, 160, H], fill=(22, 27, 34))  # BG_SIDEBAR #161B22

            # Shield polygon
            cx, cy = 80, 100
            pts = [cx, cy-40, cx+34, cy-24, cx+34, cy+10, cx, cy+46, cx-34, cy+10, cx-34, cy-24]
            filled = self._protected
            d.polygon(pts, fill=(33, 38, 45) if not filled else (28, 33, 40),
                     outline=(88, 166, 255) if not filled else (35, 165, 89), width=2)  # ACCENT_BLUE / ACCENT

            # Try to load a font, fall back to default
            try:
                font_big   = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 22)
                font_med   = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 14)
                font_small = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 11)
                font_bold  = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 16)
            except Exception:
                font_big = font_med = font_small = font_bold = ImageFont.load_default()

            # "SteamGuard" title in shield panel
            d.text((80, 158), "SteamGuard", fill=(88, 166, 255), font=font_med, anchor="mm")  # ACCENT_BLUE
            d.text((80, 175), f"v{CURRENT_VERSION}", fill=(139, 148, 158), font=font_small, anchor="mm")  # TEXT_DIM

            # Status
            status_text = "PROTECTED" if self._protected else "STANDBY"
            status_color = (35, 165, 89) if self._protected else (139, 148, 158)  # ACCENT / TEXT_DIM
            d.text((80, 195), status_text, fill=status_color, font=font_small, anchor="mm")

            # Stats panel (right)
            elapsed_secs = int((datetime.now() - self._session_start).total_seconds())
            def fmt(s):
                if s < 60: return f"{s}s"
                if s < 3600: return f"{s//60}m"
                return f"{s//3600}h {(s%3600)//60}m"

            total_heals = self._heal_count + self._rule_heal_cnt
            game_name = self._running_game["name"] if self._running_game else "No game"

            stats = [
                ("Session Time",   fmt(elapsed_secs)),
                ("Heals Fired",    str(total_heals)),
                ("Connections Cut",str(self._kill_counter)),
                ("Current Game",   game_name[:20] + "…" if len(game_name) > 20 else game_name),
            ]

            d.text((330, 30), "Session Stats", fill=(230, 237, 243), font=font_bold, anchor="mm")

            for i, (label, value) in enumerate(stats):
                y = 65 + i * 52
                d.rectangle([175, y-4, 585, y+44], fill=(28, 33, 40))  # BG_CARD #1C2128
                d.text((195, y+4), label, fill=(139, 148, 158), font=font_small)
                d.text((195, y+22), value, fill=(230, 237, 243), font=font_bold)

            # Badges row
            if self._badges:
                badge_icons = {"first_guard":"🛡","first_heal":"⚡","healer_10":"🔥",
                              "healer_100":"💎","night_watch":"🌙","founder":"⭐"}
                badge_str = " ".join(badge_icons.get(b, "?") for b in self._badges[:5])
                d.text((380, 248), badge_str, fill=(230, 237, 243), font=font_med, anchor="mm")

            # Watermark
            d.text((580, 265), "SteamGuard", fill=(48, 54, 61), font=font_small, anchor="rs")

            # Save to Desktop
            desktop = Path(os.path.expanduser("~")) / "Desktop"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = desktop / f"SteamGuard_card_{ts}.png"
            img.save(str(out_path), "PNG")
            self._log(f"Share card saved: {out_path.name}", level="success", color=GREEN)

            # Open it
            import subprocess
            subprocess.Popen(["explorer", str(out_path)], creationflags=subprocess.CREATE_NO_WINDOW)

        except ImportError:
            self._log("Pillow required for share card (pip install Pillow)", level="warn", color=YELLOW)
        except Exception as e:
            self._log(f"Share card failed: {e}", error=True)

    # ── Copy Stats to Clipboard ───────────────────────────────────────────────

    def _copy_stats_text(self):
        elapsed = int((datetime.now() - self._session_start).total_seconds())
        def fmt(s):
            if s < 60: return f"{s}s"
            if s < 3600: return f"{s//60}m {s%60}s"
            return f"{s//3600}h {(s%3600)//60}m"
        total_heals = self._heal_count + self._rule_heal_cnt
        game = self._running_game["name"] if self._running_game else "None"
        text = (f"🛡 SteamGuard v{CURRENT_VERSION}\n"
                f"Session: {fmt(elapsed)} | Heals: {total_heals} | "
                f"Connections cut: {self._kill_counter}\n"
                f"Game: {game}\n"
                f"Badges: {', '.join(self._badges) if self._badges else 'None yet'}")
        self.clipboard_clear()
        self.clipboard_append(text)
        self._log("Stats copied to clipboard.", level="success", color=GREEN)

    # ── Game detection loop ───────────────────────────────────────────────────

    def _detect_game_loop(self):
        def detect():
            game     = get_running_game(self._catalog)
            new_id   = game["appid"] if game else None
            prev_id  = self._running_appid

            # Update state regardless of change so self._running_game is always fresh
            self._running_game  = game
            self._running_appid = new_id

            if new_id != prev_id:
                if game:
                    g = game  # capture for lambdas
                    self.after(0, lambda: self._update_game_card(g))
                    self.after(0, lambda: self._log(
                        f"Detected: {g['name']} (AppID {g['appid']})"))
                    # Auto-enable protection when game launches
                    if self._auto_heal.get() and not self._protected:
                        self.after(0, self._start_protection)
                else:
                    self.after(0, lambda: self._update_game_card(None))
                    if self._protected:
                        self.after(0, self._stop_protection)

        threading.Thread(target=detect, daemon=True).start()
        # Poll every 3s while no game, 5s while game running
        interval = 5000 if self._running_appid else 3000
        self.after(interval, self._detect_game_loop)

    # ── Auto-heal callback ────────────────────────────────────────────────────

    def _on_cm_detected(self, source: str, detail: str):
        """Called from background thread when network reconnect / CM connection detected."""
        if not self._protected:
            return
        # Run the heal in a thread — _kill_valve_connections() blocks for ~1s
        threading.Thread(
            target=self._auto_heal_worker,
            args=(source, detail), daemon=True).start()

    def _auto_heal_worker(self, source: str, detail: str):
        """Background thread: re-apply block AND kill any live CM connections."""
        if not self._protected:
            return
        ok = fw_enable_fast()
        if not ok:
            ok = fw_rule_state() is True
            if not ok:
                self.after(0, lambda: self._log(
                    f"AUTO-HEAL FAILED [{source}]: {detail}", error=True))
                return
        # Kill any connection that slipped through before the block fired
        killed = _kill_valve_connections()
        self.after(0, lambda: self._auto_heal_done(source, detail, killed))

    def _auto_heal_done(self, source: str, detail: str, killed: int):
        """Main-thread callback after auto-heal completes."""
        if not self._protected:
            return
        self._heal_count += 1
        self._last_heal_time = datetime.now()
        extra = f", severed {killed} connection(s)" if killed else ""
        msg = (f"AUTO-HEAL #{self._heal_count} [{source}]  "
               f"Block re-applied{extra}. ({detail})")
        self._log(msg, level="heal")
        self._sync_fw_state(True)
        self._update_heal_badge()

        # Increment hourly heal chart
        self._hourly_heals[6] += 1
        self._redraw_chart()

        # Kill counter
        if killed:
            self.after(0, lambda n=killed: self._increment_kill_counter(n))

        # Session history
        self._append_heal_event(source, detail, killed)

        # Update stats tab values
        self._update_stats_tab()

    # ── Protection on/off ────────────────────────────────────────────────────

    def _start_protection(self):
        if not self._admin:
            self._log("Need admin to manage firewall rules.", True)
            return
        if self._protection_busy or self._protected:
            return  # already in flight or already on

        # Warn if no game is running — user needs to launch the game FIRST
        if not self._running_game:
            self._log(
                "⚠  No game detected yet. CORRECT ORDER: launch the game first, "
                "then click START PROTECTION, then your friend launches on his end.",
                error=False, color=YELLOW)
            # Don't block — user may be doing it manually and knows what they're doing

        self._protection_busy = True
        self._protect_btn.config(state="disabled",
                                 text="🛡  Activating…", bg=YELLOW)

        def worker():
            state = fw_rule_state()
            if state is None:
                if self._steam_exe:
                    ok, msg = fw_create(self._steam_exe)
                    if not ok:
                        self.after(0, lambda m=msg: (
                            self._log(f"Rule creation failed: {m}", True),
                            self._finish_protection_busy()))
                        return
                else:
                    self.after(0, lambda: (
                        self._log("Steam.exe not found — can't create rule.", True),
                        self._finish_protection_busy()))
                    return
            ok = fw_enable_fast()
            # Verify the rule actually enabled (netsh can silently fail)
            if ok:
                confirmed = fw_rule_state() is True
                ok = confirmed
            if ok:
                # CRITICAL: firewall only blocks NEW connections — existing CM
                # connections stay alive and can still deliver the lock signal.
                # Must kill the live connection so Steam reconnects through the
                # now-active block and goes offline.
                killed = _kill_valve_connections()
                if killed:
                    self.after(0, lambda n=killed: self._log(
                        f"Severed {n} live Valve CM connection(s) — Steam now offline.",
                        color=YELLOW))
                    self.after(0, lambda n=killed: self._increment_kill_counter(n))
            self.after(0, lambda: self._on_protection_started(ok))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_protection_busy(self):
        """Reset the busy flag and restore the button to idle state."""
        self._protection_busy = False
        self._update_protect_btn()

    def _on_protection_started(self, ok: bool):
        self._protection_busy = False
        if ok:
            self._protected = True
            self._protect_start = datetime.now()
            self._monitor.set_active(True)
            self._sync_fw_state(True)
            self._update_protect_btn()
            self._update_status_banner()
            game_name = self._running_game["name"] if self._running_game else "manual"
            self._log(f"Protection ON  —  {game_name}. Auto-heal watching network.",
                      level="success")
            _play_protect_sound()
            _show_toast("SteamGuard — Protected",
                        f"Library lock blocked for {game_name}. Tell your friend to launch now.")
            # Update rich presence
            if self._rpc is not None:
                try:
                    self._rpc.update(
                        state=f"Playing {game_name}",
                        details="Protected via SteamGuard",
                        large_image="shield",
                        start=int(self._session_start.timestamp())
                    )
                except Exception:
                    pass
        else:
            self._update_protect_btn()
            self._log("Failed to enable firewall rule — check admin rights.", True)

    def _stop_protection(self):
        if self._protection_busy:
            return
        self._protection_busy = True
        self._protect_btn.config(state="disabled",
                                 text="🛡  Stopping…", bg=TEXT_DIM)

        def worker():
            ok = fw_disable_fast()
            self.after(0, lambda: self._on_protection_stopped(ok))

        threading.Thread(target=worker, daemon=True).start()

    def _on_protection_stopped(self, ok: bool):
        self._protection_busy = False
        self._protected = False
        self._protect_start = None
        self._monitor.set_active(False)
        self._sync_fw_state(False)
        self._update_protect_btn()
        self._update_status_banner()
        self._log("Protection OFF  —  Steam can connect normally.")
        _play_unprotect_sound()
        _show_toast("SteamGuard — Unprotected", "Steam can now reach Valve servers.")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Custom draggable title bar ─────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG_SIDEBAR, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # Shield canvas icon (36x36)
        hc = tk.Canvas(hdr, width=36, height=36, bg=BG_SIDEBAR, highlightthickness=0)
        hc.place(x=14, y=10)
        hc.create_polygon(18, 3, 33, 9, 33, 21, 18, 33, 3, 21, 3, 9,
                          fill=ACCENT, outline="", smooth=False)
        hc.create_text(18, 19, text="S", fill="white", font=("Segoe UI", 12, "bold"))

        title_lbl = tk.Label(hdr, text="SteamGuard", bg=BG_SIDEBAR, fg=TEXT_MAIN,
                             font=F_TITLE)
        title_lbl.place(x=58, y=12)

        ver_lbl = tk.Label(hdr, text=f"v{CURRENT_VERSION}", bg=BG_SIDEBAR, fg=TEXT_DIM, font=F_SMALL)
        ver_lbl.place(x=178, y=18)

        # Admin pill canvas (70x20)
        pill_cv = tk.Canvas(hdr, width=70, height=20, bg=BG_SIDEBAR, highlightthickness=0)
        pill_cv.place(relx=1.0, x=-140, y=18)
        if self._admin:
            pill_color = GREEN
            pill_text  = "✓ ADMIN"
        else:
            pill_color = YELLOW
            pill_text  = "⚠ NO ADMIN"
        pill_cv.create_rectangle(0, 0, 70, 20, fill=pill_color, outline="")
        pill_cv.create_text(35, 10, text=pill_text, fill=BG_DARK,
                            font=("Segoe UI", 7, "bold"))

        # Minimize button
        min_btn = tk.Label(hdr, text="─", bg=BG_SIDEBAR, fg=TEXT_DIM,
                           font=("Segoe UI", 12), cursor="hand2")
        min_btn.place(relx=1.0, x=-64, y=16)
        min_btn.bind("<Button-1>", lambda e: self._minimize())
        min_btn.bind("<Enter>", lambda e: min_btn.config(fg=TEXT_MAIN))
        min_btn.bind("<Leave>", lambda e: min_btn.config(fg=TEXT_DIM))

        # Close button
        close_btn = tk.Label(hdr, text="✕", bg=BG_SIDEBAR, fg=RED,
                             font=("Segoe UI", 12), cursor="hand2")
        close_btn.place(relx=1.0, x=-32, y=16)
        close_btn.bind("<Button-1>", lambda e: self._on_close())
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg="#ff6b6b"))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg=RED))

        # Drag bindings
        def _drag_start(e):
            self._drag_x = e.x_root - self.winfo_x()
            self._drag_y = e.y_root - self.winfo_y()

        def _drag_motion(e):
            self.geometry(f"+{e.x_root - self._drag_x}+{e.y_root - self._drag_y}")

        for widget in (hdr, title_lbl, ver_lbl):
            widget.bind("<ButtonPress-1>", _drag_start)
            widget.bind("<B1-Motion>", _drag_motion)

        # 1px border separator at bottom of header (title bar)
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ── Animated shield canvas (centrepiece) ──────────────────────────────
        SHIELD_W, SHIELD_H = 520, 150
        self._shield_cv = tk.Canvas(self, width=SHIELD_W, height=SHIELD_H,
                                    bg=BG_DARK, highlightthickness=0)
        self._shield_cv.pack(fill="x")

        cx, cy = SHIELD_W // 2, SHIELD_H // 2 + 4

        # Pulse rings (drawn behind shield, updated by animation)
        ring_colors = [GREEN, ACCENT, PURPLE]
        self._ring_ids = [
            self._shield_cv.create_oval(0, 0, 0, 0, outline=ring_colors[i],
                                        width=2, state="hidden")
            for i in range(3)
        ]

        # Shield body — large polygon
        def shield_pts(cx, cy, w, h):
            hw, hh = w//2, h//2
            return [cx, cy-hh,
                    cx+hw, cy-hh+h//5,
                    cx+hw, cy+hh//3,
                    cx, cy+hh,
                    cx-hw, cy+hh//3,
                    cx-hw, cy-hh+h//5]

        SW, SH = 76, 86
        self._shield_body = self._shield_cv.create_polygon(
            *shield_pts(cx, cy, SW, SH),
            fill=BG_ELEVATED, outline=ACCENT, width=2, smooth=False)
        self._shield_letter = self._shield_cv.create_text(
            cx, cy+2, text="S", fill=ACCENT,
            font=("Segoe UI", 28, "bold"))

        # Status text under shield
        self._shield_status_txt = self._shield_cv.create_text(
            cx, cy + SH//2 + 16, text="UNPROTECTED",
            fill=TEXT_DIM, font=("Segoe UI", 9, "bold"))

        # 8 orbiting spark particles
        self._spark_ids = [
            self._shield_cv.create_oval(0, 0, 4, 4, fill=ACCENT, outline="", state="hidden")
            for _ in range(8)
        ]

        # ── 3-column status indicators ────────────────────────────────────────
        status_outer = tk.Frame(self, bg=BORDER)   # BORDER bg shows through as dividers
        status_outer.pack(fill="x", padx=12, pady=(2, 0))

        self._status_cards = []

        def _make_indicator(parent, label, add_divider):
            if add_divider:
                tk.Frame(parent, bg=BORDER, width=1).pack(side="left", fill="y")
            card = tk.Frame(parent, bg=BG_CARD)
            card.pack(side="left", fill="both", expand=True)
            tk.Label(card, text=label, bg=BG_CARD, fg=TEXT_DIM,
                     font=F_LABEL).pack(pady=(12, 2), padx=10)
            dot_cv = tk.Canvas(card, width=12, height=12, bg=BG_CARD,
                               highlightthickness=0)
            dot_cv.pack()
            dot = dot_cv.create_oval(1, 1, 11, 11, fill=TEXT_DIM, outline="")
            lbl = tk.Label(card, text="—", bg=BG_CARD, fg=TEXT_DIM,
                           font=("Segoe UI", 8, "bold"))
            lbl.pack(pady=(2, 12), padx=10)
            self._status_cards.append(card)
            return dot_cv, dot, lbl

        self._fw_dot_cv,   self._fw_dot,   self._fw_lbl   = _make_indicator(status_outer, "FIREWALL", False)
        self._heal_dot_cv, self._heal_dot, self._heal_count_lbl = _make_indicator(status_outer, "AUTO-HEAL", True)
        self._net_dot_cv,  self._net_dot,  self._net_lbl  = _make_indicator(status_outer, "NETWORK", True)
        # Feature B: 4th LIBRARY card
        self._lib_dot_cv,  self._lib_dot,  self._lib_lbl  = _make_indicator(status_outer, "LIBRARY", True)

        # Add tooltips to indicators
        Tooltip(self._fw_dot_cv,   "Firewall rule status: BLOCKING = active protection")
        Tooltip(self._heal_dot_cv, "Auto-heal count: times the block was re-applied after reconnect")
        Tooltip(self._net_dot_cv,  "Internet connectivity status")
        Tooltip(self._lib_dot_cv,  "Shared library status: LOCKED means someone else is using it")

        # Section separator after status row
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", pady=(8, 0))

        # ── Game detection card ───────────────────────────────────────────────
        game_section = tk.Frame(self, bg=BG_DARK)
        game_section.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(game_section, text="DETECTED GAME", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(0, 3))

        # 2px BORDER outline; border recoloured ACCENT when a game is detected
        self._game_card = tk.Frame(game_section, bg=BG_CARD,
                                   highlightbackground=BORDER,
                                   highlightcolor=BORDER, highlightthickness=2,
                                   bd=0)
        self._game_card.pack(fill="x")

        # Top row: icon + text + badge
        game_top = tk.Frame(self._game_card, bg=BG_CARD)
        game_top.pack(fill="x")

        self._game_icon = tk.Label(game_top, text="🎮", bg=BG_CARD,
                                   font=("Segoe UI Emoji", 20),
                                   compound="center")
        self._game_icon.pack(side="left", padx=(14, 10), pady=10)

        game_text = tk.Frame(game_top, bg=BG_CARD)
        game_text.pack(side="left", fill="x", expand=True, pady=12)
        self._game_name_lbl = tk.Label(game_text, text="Waiting for game to launch…",
                                       bg=BG_CARD, fg=TEXT_DIM, font=F_HEAD, anchor="w")
        self._game_name_lbl.pack(fill="x")
        self._game_meta_lbl = tk.Label(game_text, text="Launch a Steam game to begin",
                                       bg=BG_CARD, fg=TEXT_DIM, font=F_SMALL, anchor="w")
        self._game_meta_lbl.pack(fill="x")

        # Feature C: session playtime countdown banner inside game card
        self._playing_lbl = tk.Label(game_text, text="", bg=BG_CARD,
                                     fg=TEXT_DIM, font=F_SMALL, anchor="w")
        self._playing_lbl.pack(fill="x")

        self._game_badge = tk.Label(game_top, text="", bg=BG_CARD,
                                    fg=TEXT_DIM, font=("Segoe UI", 7, "bold"))
        self._game_badge.pack(side="right", padx=12)

        # ── Game badge panel (shareable / VAC / 3rd-party badges) ─────────────
        self._badge_panel = GameBadgePanel(game_section)
        self._badge_panel.pack(fill="x", pady=(2, 0))

        # Section separator after game card
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(8, 0))

        # ── Feature A: Playtime tracker pills ─────────────────────────────────
        pills_outer = tk.Frame(self, bg=BG_DARK)
        pills_outer.pack(fill="x", padx=12, pady=(8, 0))

        def _make_pill(parent, label, init_val):
            pill = tk.Frame(parent, bg=BG_CARD)
            pill.pack(side="left", fill="both", expand=True, padx=(0, 6))
            tk.Label(pill, text=label, bg=BG_CARD, fg=TEXT_DIM,
                     font=F_LABEL).pack(pady=(8, 0), padx=10)
            val = tk.Label(pill, text=init_val, bg=BG_CARD, fg=TEXT_MAIN,
                           font=("Segoe UI", 11, "bold"))
            val.pack(pady=(0, 8), padx=10)
            return pill, val

        _, self._pill_session_val   = _make_pill(pills_outer, "SESSION",   "0h 0m")
        _, self._pill_protected_val = _make_pill(pills_outer, "PROTECTED", "0h 0m")
        _, self._pill_heals_val     = _make_pill(pills_outer, "HEALS",     "0")
        # Last pill: no right padding
        pills_outer.winfo_children()[-1].pack_configure(padx=0)
        self._after_pills_id = self.after(1000, self._update_playtime_pills)

        # ── Big protect button ────────────────────────────────────────────────
        self._protect_btn = tk.Button(
            self, text="🛡  START PROTECTION",
            bg=ACCENT, fg="white",
            font=("Segoe UI", 12, "bold"),
            relief="raised", bd=2, cursor="hand2",
            activebackground=BG_ELEVATED,
            command=self._on_protect_toggle)
        self._protect_btn.pack(fill="x", padx=12, pady=(12, 0), ipady=12)
        self._protect_btn.bind("<Enter>", lambda e: self._hover_enter(self._protect_btn, BG_ELEVATED))
        self._protect_btn.bind("<Leave>", lambda e: self._update_protect_btn())
        Tooltip(self._protect_btn, "Toggle Steam CM firewall block on/off")

        # ── Session stats strip ───────────────────────────────────────────────
        stats_row = tk.Frame(self, bg=BG_DARK)
        stats_row.pack(fill="x", padx=12, pady=(4, 0))
        self._stats_lbl = tk.Label(
            stats_row, text="Session: 0s  |  Heals: 0  |  Last heal: —",
            bg=BG_DARK, fg=TEXT_DIM, font=("Segoe UI", 8), anchor="w")
        self._stats_lbl.pack(side="left")
        self._after_stats_id = self.after(1000, self._update_stats_strip)

        # ── Kill counter row ──────────────────────────────────────────────────
        # (kill counter moved above event log — see EVENTS section below)

        # ── Tabbed notebook ───────────────────────────────────────────────────
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.TNotebook", background=BG_DARK, borderwidth=0)
        style.configure("Dark.TNotebook.Tab", background=BG_CARD, foreground=TEXT_DIM,
                        font=F_SMALL, padding=[12, 5])
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", BG_PANEL)],
                  foreground=[("selected", TEXT_MAIN)])


        # ── Control toolbar (was Settings tab) ──────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=0, pady=(6,0))
        ctrl = tk.Frame(self, bg=BG_DARK)
        ctrl.pack(fill="x", padx=12, pady=(4, 2))

        # Row 1: checkboxes + scan + help
        ctrl_r1 = tk.Frame(ctrl, bg=BG_DARK)
        ctrl_r1.pack(fill="x")
        tk.Checkbutton(ctrl_r1, text="Auto-protect", variable=self._auto_heal,
                       bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL,
                       selectcolor=BG_CARD, activebackground=BG_DARK,
                       activeforeground=TEXT_MAIN,
                       highlightthickness=0).pack(side="left")
        tk.Checkbutton(ctrl_r1, text="Start with Windows", variable=self._autostart_var,
                       bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL,
                       selectcolor=BG_CARD, activebackground=BG_DARK,
                       activeforeground=TEXT_MAIN, highlightthickness=0,
                       command=self._on_autostart_toggle).pack(side="left", padx=(8,0))
        _hb = tk.Button(ctrl_r1, text="? Help", bg=BG_CARD, fg=ACCENT,
                        font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                        activebackground=BORDER, command=self._show_help)
        _hb.pack(side="right", ipady=3, ipadx=6)
        _hb.bind("<Enter>", lambda e: self._hover_enter(_hb, BORDER))
        _hb.bind("<Leave>", lambda e: self._hover_leave(_hb, BG_CARD))
        _sb = tk.Button(ctrl_r1, text="Scan", bg=BG_CARD, fg=TEXT_DIM,
                        font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                        activebackground=BORDER, command=self._rescan_games)
        _sb.pack(side="right", ipady=3, ipadx=6, padx=(0,4))
        _sb.bind("<Enter>", lambda e: self._hover_enter(_sb, BORDER))
        _sb.bind("<Leave>", lambda e: self._hover_leave(_sb, BG_CARD))
        Tooltip(_sb, "Re-scan Steam library for installed games")

        # Row 2: tool buttons — 2-row grid
        def _make_tool_btn(parent, label, cmd, tip, is_family=False):
            b = tk.Button(parent, text=label, bg=BG_CARD, fg=TEXT_DIM,
                          font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                          activebackground=BG_ELEVATED, command=cmd)
            b.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 4))
            def _enter(e, bb=b):
                bb.config(bg=BG_ELEVATED, fg=TEXT_MAIN)
            def _leave(e, bb=b):
                # Family lock keeps RED when active
                if is_family and self._family_lock_active:
                    bb.config(bg=RED, fg="white")
                else:
                    bb.config(bg=BG_CARD, fg=TEXT_DIM)
            b.bind("<Enter>", _enter)
            b.bind("<Leave>", _leave)
            Tooltip(b, tip)
            return b

        tool_grid_r1 = tk.Frame(ctrl, bg=BG_DARK)
        tool_grid_r1.pack(fill="x", pady=(4, 0))
        _make_tool_btn(tool_grid_r1, "🔒 Why Locked?",
                       lambda: WhyLockedDialog(self, self._steam_dir),
                       "See who locked your shared library")
        _make_tool_btn(tool_grid_r1, "💿 DLC Advisor",
                       lambda: DLCAdvisorDialog(self, self._steam_dir),
                       "Check DLC sharing coverage")
        _make_tool_btn(tool_grid_r1, "🖥 Deck Health",
                       lambda: SteamDeckHealthDialog(self, self._steam_dir),
                       "Verify Steam Deck sharing setup")
        # Last in row: no right padding
        tool_grid_r1.winfo_children()[-1].pack_configure(padx=0)

        tool_grid_r2 = tk.Frame(ctrl, bg=BG_DARK)
        tool_grid_r2.pack(fill="x", pady=(4, 0))
        _make_tool_btn(tool_grid_r2, "📡 Offline Ready",
                       lambda: OfflineReadinessDialog(self, self._steam_dir),
                       "Check offline play readiness")
        _make_tool_btn(tool_grid_r2, "📅 Cooldown",
                       lambda: FamilyCooldownDialog(self, self._steam_dir),
                       "Simulate cooldown scenarios")
        _make_tool_btn(tool_grid_r2, "📤 Share Card",
                       self._export_share_card,
                       "Export PNG share card")
        _make_tool_btn(tool_grid_r2, "📋 Copy Stats",
                       self._copy_stats_text,
                       "Copy session stats to clipboard")
        tool_grid_r2.winfo_children()[-1].pack_configure(padx=0)

        # Row 3: Family Lock (Feature D) + Export Log
        tool_grid_r3 = tk.Frame(ctrl, bg=BG_DARK)
        tool_grid_r3.pack(fill="x", pady=(4, 0))
        self._family_lock_btn = _make_tool_btn(
            tool_grid_r3, "🔐 Family Lock",
            self._open_family_lock,
            "Quick-lock Steam for a set number of minutes (educational)",
            is_family=True)
        _make_tool_btn(tool_grid_r3, "📄 Export Log",
                       self._export_log,
                       "Save event log to Desktop")
        tool_grid_r3.winfo_children()[-1].pack_configure(padx=0)

        # Heartbeat status label (needed by heartbeat loop)
        self._heartbeat_status_lbl = tk.Label(ctrl, text="● Server sync: —",
            bg=BG_DARK, fg=TEXT_DIM, font=("Segoe UI", 7), anchor="w")
        self._heartbeat_status_lbl.pack(anchor="w", pady=(2,0))

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=0, pady=(2,0))

        nb = ttk.Notebook(self, style="Dark.TNotebook")
        nb.pack(fill="both", expand=True, padx=0, pady=(8, 0))

        # ── Tab 1: Events ──────────────────────────────────────────────────────
        events_frame = tk.Frame(nb, bg=BG_DARK)
        nb.add(events_frame, text="EVENTS")

        log_outer = tk.Frame(events_frame, bg=BG_DARK)
        log_outer.pack(fill="both", expand=True, padx=12, pady=(8, 8))

        # Header row: EVENTS label (left) + kill counter (right-aligned)
        log_head = tk.Frame(log_outer, bg=BG_DARK)
        log_head.pack(fill="x", pady=(0, 4))
        tk.Label(log_head, text="EVENTS", bg=BG_DARK, fg=TEXT_DIM,
                 font=F_LABEL).pack(side="left")
        self._kill_lbl = tk.Label(log_head, text="⚡ Connections severed: 0",
                                  bg=BG_DARK, fg=TEXT_DIM, font=("Segoe UI", 8), anchor="e")
        self._kill_lbl.pack(side="right")

        self._log_w = scrolledtext.ScrolledText(
            log_outer, bg=BG_BASE, fg=TEXT_MAIN, font=F_MONO, height=11,
            relief="flat", bd=0, state="disabled", wrap="word")
        self._log_w.pack(fill="both", expand=True)

        # ── Admin warning banner ──────────────────────────────────────────────
        if not self._admin:
            warn = tk.Frame(self, bg=YELLOW, cursor="hand2")
            warn.pack(fill="x")
            lbl = tk.Label(warn,
                text="⚠  Not Administrator — firewall changes will fail.  "
                     "Click to re-launch elevated.",
                bg=YELLOW, fg="#0d1117", font=F_SMALL)
            lbl.pack(pady=4)
            for w in (warn, lbl):
                w.bind("<Button-1>", lambda e: elevate())

        # Start network status poller
        self._poll_net_status()
        # Feature B: start library locked status detector (5s poll)
        self._after_library_id = self.after(2000, self._poll_library_status)

        # ── Keyboard shortcuts ────────────────────────────────────────────────
        self.bind("<Control-p>", lambda e: self._on_protect_toggle())
        self.bind("<Control-l>", lambda e: (self._log_w.config(state="normal"),
                                             self._log_w.delete("1.0", "end"),
                                             self._log_w.config(state="disabled")))
        self.bind("<Control-e>", lambda e: self._export_log())
        self.bind("<Control-s>", lambda e: self._rescan_games())
        self.bind("<F1>",        lambda e: self._show_help())
        self.bind("<Escape>",    lambda e: self._minimize())

    # ── Chart drawing ─────────────────────────────────────────────────────────

    def _redraw_chart(self):
        """Stats tab removed — no-op kept so call sites don't crash."""
        pass

    # ── Stats tab updater ─────────────────────────────────────────────────────

    def _update_stats_tab(self):
        """Stats tab removed — no-op kept so call sites don't crash."""
        pass

    # ── Game art ──────────────────────────────────────────────────────────────

    def _fetch_game_art(self, appid: int):
        def worker():
            # Try capsule image first (smaller), fall back to header
            for url in [
                f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/capsule_231x87.jpg",
                f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg",
            ]:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "SteamGuard/1.3"})
                    with urllib.request.urlopen(req, timeout=5) as r:
                        data = r.read()
                    try:
                        from PIL import Image, ImageTk
                        import io as _io
                        img = Image.open(_io.BytesIO(data)).resize((116, 44), Image.LANCZOS)
                        photo = ImageTk.PhotoImage(img)
                        self.after(0, lambda p=photo: self._set_game_art(p))
                        return
                    except ImportError:
                        # Pillow not installed — show emoji fallback
                        self.after(0, lambda: self._game_icon.config(image="", text="🎮"))
                        return
                except Exception:
                    continue
            # Both URLs failed
            self.after(0, lambda: self._game_icon.config(image="", text="🎮"))
        threading.Thread(target=worker, daemon=True).start()

    def _set_game_art(self, photo):
        self._game_art_photo = photo  # keep reference
        try:
            self._game_icon.config(image=photo, text="")
        except Exception:
            pass

    # ── Session history ───────────────────────────────────────────────────────

    def _append_heal_event(self, source: str, detail: str, killed: int):
        try:
            history = []
            if self._session_history_file.exists():
                history = json.loads(self._session_history_file.read_text("utf-8"))
            history.append({
                "ts": datetime.now().isoformat(),
                "source": source,
                "detail": detail,
                "killed": killed,
                "game": self._running_game["name"] if self._running_game else "unknown"
            })
            # Keep last 500 events
            history = history[-500:]
            self._session_history_file.write_text(json.dumps(history, indent=2), "utf-8")
        except Exception:
            pass

    # ── Export log ────────────────────────────────────────────────────────────

    def _export_log(self):
        import tkinter.filedialog as fd
        desktop = Path(os.path.expanduser("~")) / "Desktop"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = desktop / f"SteamGuard_log_{ts}.txt"
        path = fd.asksaveasfilename(
            initialfile=str(default.name),
            initialdir=str(desktop),
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Export Event Log")
        if not path:
            return
        try:
            content = self._log_w.get("1.0", "end")
            header = f"SteamGuard v{CURRENT_VERSION} — Event Log\nExported: {datetime.now()}\n{'='*60}\n"
            Path(path).write_text(header + content, encoding="utf-8")
            self._log(f"Log exported to {path}", level="success")
        except Exception as e:
            self._log(f"Export failed: {e}", error=True)

    # ── UI updates ────────────────────────────────────────────────────────────

    def _sync_fw_state(self, state: bool | None):
        self._fw_state = state
        if state is True:  # rule enabled = blocking Steam CM traffic
            color, text = RED, "BLOCKING"
        elif state is False:  # rule exists but disabled — not protected
            color, text = YELLOW, "INACTIVE"
        else:  # None — rule doesn't exist yet
            color, text = TEXT_DIM, "NO RULE"
        self._fw_dot_cv.itemconfig(self._fw_dot, fill=color)
        self._fw_lbl.config(text=text, fg=color)

    def _update_heal_badge(self):
        total = self._heal_count + self._rule_heal_cnt
        self._heal_dot_cv.itemconfig(self._heal_dot,
                                     fill=PURPLE if total else TEXT_DIM)
        self._heal_count_lbl.config(
            text=f"{total} heal{'s' if total != 1 else ''}",
            fg=PURPLE if total else TEXT_DIM)

    def _update_status_banner(self):
        if self._protected:
            # Update shield to green + start pulse animation
            self._shield_cv.itemconfig(self._shield_body,
                                       fill=BG_CARD, outline=GREEN)
            self._shield_cv.itemconfig(self._shield_letter, fill=GREEN)
            self._shield_cv.itemconfig(self._shield_status_txt,
                                       text="PROTECTED", fill=GREEN)
            if not self._anim_running:
                self._anim_running = True
                self._anim_tick()
        else:
            # Snap back to blue/dim, stop animation
            self._anim_running = False
            self._shield_cv.itemconfig(self._shield_body,
                                       fill=BG_ELEVATED, outline=ACCENT)
            self._shield_cv.itemconfig(self._shield_letter, fill=ACCENT)
            self._shield_cv.itemconfig(self._shield_status_txt,
                                       text="UNPROTECTED", fill=TEXT_DIM)
            for rid in self._ring_ids:
                self._shield_cv.itemconfig(rid, state="hidden")
            for sid in self._spark_ids:
                self._shield_cv.itemconfig(sid, state="hidden")

    def _update_stats_strip(self):
        def _fmt(secs: int) -> str:
            if secs < 60:   return f"{secs}s"
            if secs < 3600: return f"{secs//60}m {secs%60}s"
            return f"{secs//3600}h {(secs%3600)//60}m"

        elapsed = int((datetime.now() - self._session_start).total_seconds())
        total_heals = self._heal_count + self._rule_heal_cnt
        if self._last_heal_time:
            ago = int((datetime.now() - self._last_heal_time).total_seconds())
            last = f"{ago}s ago" if ago < 3600 else self._last_heal_time.strftime("%H:%M")
        else:
            last = "—"

        parts = [f"Session: {_fmt(elapsed)}", f"Heals: {total_heals}", f"Last heal: {last}"]

        # Protection timer
        if self._protected and self._protect_start:
            pt = int((datetime.now() - self._protect_start).total_seconds())
            parts.insert(1, f"Protected: {_fmt(pt)}")
            # Also update the shield canvas status text with live timer
            try:
                self._shield_cv.itemconfig(
                    self._shield_status_txt,
                    text=f"PROTECTED  {_fmt(pt)}")
            except Exception:
                pass

        self._stats_lbl.config(text="  |  ".join(parts))
        self._after_stats_id = self.after(1000, self._update_stats_strip)

        # Also refresh stats tab
        self._update_stats_tab()

    def _update_game_card(self, game: dict | None):
        if game:
            self._game_name_lbl.config(
                text=game["name"], fg=TEXT_MAIN)
            self._game_meta_lbl.config(
                text=f"AppID {game['appid']}  •  detected via {game.get('method','?')}",
                fg=TEXT_DIM)
            self._game_badge.config(text="● ACTIVE", fg=GREEN)
            # Game card border glows ACCENT green when a game is detected
            try:
                self._game_card.config(highlightbackground=ACCENT,
                                       highlightcolor=ACCENT)
            except Exception:
                pass
            # Feature C: start/keep the "Playing for" timer
            if self._game_detected_at is None:
                self._game_detected_at = datetime.now()
                if self._after_playing_id is None:
                    self._after_playing_id = self.after(1000, self._update_playing_timer)
            self._fetch_game_art(game["appid"])
            self._badge_panel.update_game(game["appid"], game["name"])
            # Update rich presence
            if self._rpc is not None:
                try:
                    self._rpc.update(
                        state=f"Playing {game['name']}",
                        details="Protected via SteamGuard",
                        large_image="shield",
                        start=int(self._session_start.timestamp())
                    )
                except Exception:
                    pass
        else:
            self._game_name_lbl.config(
                text="No game running", fg=TEXT_DIM)
            self._game_meta_lbl.config(
                text="Launch a Steam game to begin", fg=TEXT_DIM)
            self._game_badge.config(text="", fg=TEXT_DIM)
            self._game_icon.config(image="", text="🎮")
            self._game_art_photo = None
            self._badge_panel.update_game(None, "")
            # Reset game card border to neutral
            try:
                self._game_card.config(highlightbackground=BORDER,
                                       highlightcolor=BORDER)
            except Exception:
                pass
            # Feature C: clear the playing timer
            self._game_detected_at = None
            if self._playing_lbl is not None:
                self._playing_lbl.config(text="")

    def _anim_tick(self):
        """
        Animation tick — runs every 50 ms while protection is active.

        Effects:
        1. Shield glow: the outline colour brightness pulses smoothly
           between dim-green and bright-green using a sine wave.
        2. Pulse rings: three concentric ovals expand outward from the
           shield and fade out, staggered 800 ms apart, looping every
           2.4 s. Each ring uses a different color (GREEN, ACCENT, PURPLE).
        3. Spark particles: 8 small ovals orbit around the shield.
        """
        if not self._anim_running or not self._app_running:
            return

        import math
        import time as _time

        SHIELD_W, SHIELD_H = 520, 150
        cx, cy = SHIELD_W // 2, SHIELD_H // 2 + 4
        SW, SH = 76, 86

        # ── Glow pulse: sine over ~2 s period ─────────────────────────────
        t = _time.monotonic()
        glow = (math.sin(t * math.pi) + 1) / 2          # 0.0 – 1.0
        r = int(0x1a + glow * (0x44 - 0x1a))
        g = int(0x8a + glow * (0xff - 0x8a))
        b = int(0x1a + glow * (0x1a))
        outline_col = f"#{r:02x}{g:02x}{b:02x}"
        self._shield_cv.itemconfig(self._shield_body, outline=outline_col)
        self._shield_cv.itemconfig(self._shield_letter, fill=outline_col)

        # ── Pulse rings: each ring lives for 2.4 s, staggered 0.8 s ──────
        RING_PERIOD = 2.4
        RING_MAX_R  = 68   # max extra radius beyond shield edge
        BASE_R_X    = SW // 2 + 4
        BASE_R_Y    = SH // 2 + 4
        ring_colors = [GREEN, ACCENT, PURPLE]

        for i, rid in enumerate(self._ring_ids):
            phase = ((t + i * (RING_PERIOD / len(self._ring_ids)))
                     % RING_PERIOD) / RING_PERIOD     # 0.0 – 1.0
            rx = BASE_R_X + phase * RING_MAX_R
            ry = BASE_R_Y + phase * RING_MAX_R
            alpha = max(0.0, 1.0 - phase * 1.6)       # fade out by ~62%

            # Each ring has its own color
            base_col = ring_colors[i]
            r_int = int(int(base_col[1:3], 16) * alpha)
            g_int = int(int(base_col[3:5], 16) * alpha)
            b_int = int(int(base_col[5:7], 16) * alpha)
            col = f"#{r_int:02x}{g_int:02x}{b_int:02x}"

            self._shield_cv.coords(rid,
                cx - rx, cy - ry, cx + rx, cy + ry)
            self._shield_cv.itemconfig(rid,
                outline=col, state="normal" if alpha > 0.02 else "hidden")

        # ── Spark particles: 8 small ovals orbiting the shield ────────────
        spark_colors = [ACCENT, PURPLE]
        for i, sid in enumerate(self._spark_ids):
            angle = (t * 1.2 + i * math.pi / 4) % (2 * math.pi)
            rx = 52 + math.cos(angle) * 3
            ry = 58 + math.sin(angle) * 3
            sx = cx + math.cos(angle) * rx - 2
            sy = cy + math.sin(angle) * ry - 2
            col = spark_colors[i % 2]
            self._shield_cv.coords(sid, sx, sy, sx + 4, sy + 4)
            self._shield_cv.itemconfig(sid, fill=col, state="normal")

        self.after(50, self._anim_tick)

    def _update_protect_btn(self):
        if self._protected:
            # Active: elevated surface w/ GREEN text
            self._protect_btn.config(
                state="normal",
                text="✓  PROTECTION ACTIVE",
                bg=BG_ELEVATED, fg=GREEN, activebackground=BG_CARD,
                relief="raised", bd=2)
            self._protect_btn.bind("<Enter>", lambda e: self._hover_enter(self._protect_btn, BG_CARD))
            self._protect_btn.bind("<Leave>", lambda e: self._hover_leave(self._protect_btn, BG_ELEVATED))
        else:
            # Inactive: accent-green call-to-action
            self._protect_btn.config(
                state="normal",
                text="🛡  START PROTECTION",
                bg=ACCENT, fg="white", activebackground=BG_ELEVATED,
                relief="raised", bd=2)
            self._protect_btn.bind("<Enter>", lambda e: self._hover_enter(self._protect_btn, BG_ELEVATED))
            self._protect_btn.bind("<Leave>", lambda e: self._hover_leave(self._protect_btn, ACCENT))

    def _on_protect_toggle(self):
        if self._protected:
            # Require confirmation before stopping — accidental stop = Steam reconnects
            import tkinter.messagebox as mb
            if not mb.askyesno(
                    "Stop Protection?",
                    "Stopping protection allows Steam to reconnect to Valve servers.\n\n"
                    "Your friend will get the 'Shared Library Locked' message.\n\n"
                    "Are you sure you want to stop?",
                    icon="warning"):
                return
            self._stop_protection()
        else:
            self._start_protection()

    def _poll_net_status(self):
        """Update network dot every 3 seconds."""
        def check():
            online = has_internet()
            color = GREEN if online else TEXT_DIM
            text  = "Online" if online else "Offline"
            self.after(0, lambda: (
                self._net_dot_cv.itemconfig(self._net_dot, fill=color),
                self._net_lbl.config(text=text, fg=color)
            ))
        threading.Thread(target=check, daemon=True).start()
        self.after(3000, self._poll_net_status)


    # ── Feature A: Playtime tracker pills (30s update) ─────────────────────────

    def _update_playtime_pills(self):
        """Refresh the SESSION / PROTECTED / HEALS pills."""
        if not self._app_running:
            return

        def _fmt_hm(secs: int) -> str:
            secs = max(0, int(secs))
            h = secs // 3600
            m = (secs % 3600) // 60
            return f"{h}h {m}m"

        try:
            sess = (datetime.now() - self._session_start).total_seconds()
            if self._pill_session_val is not None:
                self._pill_session_val.config(text=_fmt_hm(sess))

            if self._protected and self._protect_start:
                prot = (datetime.now() - self._protect_start).total_seconds()
            else:
                prot = 0
            if self._pill_protected_val is not None:
                self._pill_protected_val.config(text=_fmt_hm(prot))

            total_heals = self._heal_count + self._rule_heal_cnt
            if self._pill_heals_val is not None:
                self._pill_heals_val.config(text=str(total_heals))
        except Exception:
            pass

        self._after_pills_id = self.after(30000, self._update_playtime_pills)

    # ── Feature B: Library locked status detector (5s poll) ────────────────────

    def _poll_library_status(self):
        """Detect whether the shared Steam library appears locked.

        Simple heuristic via registry:
          - ActiveUser non-zero  AND  RunningAppID == 0  -> likely LOCKED
          - otherwise (a game is running / user active)   -> FREE
          - registry unavailable / error                  -> unknown
        """
        if not self._app_running:
            return

        status = "unknown"
        try:
            if winreg is not None:
                subkey = r"SOFTWARE\Valve\Steam\ActiveProcess"
                active_user = _reg_get(winreg.HKEY_CURRENT_USER, subkey, "ActiveUser")
                running_appid = get_running_appid_reg()
                if active_user is not None:
                    if int(active_user) != 0 and (running_appid in (None, 0)):
                        status = "locked"
                    else:
                        status = "free"
        except Exception:
            status = "unknown"

        self._library_status = status
        try:
            if status == "locked":
                color, text = RED, "LOCKED"
            elif status == "free":
                color, text = GREEN, "FREE"
            else:
                color, text = TEXT_DIM, "—"
            if self._lib_dot_cv is not None:
                self._lib_dot_cv.itemconfig(self._lib_dot, fill=color)
            if self._lib_lbl is not None:
                self._lib_lbl.config(text=text, fg=color)
        except Exception:
            pass

        self._after_library_id = self.after(5000, self._poll_library_status)

    # ── Feature C: Session playtime countdown (1s update) ──────────────────────

    def _update_playing_timer(self):
        """Update the 'Playing for: Xm Ys' banner inside the game card."""
        if not self._app_running:
            return
        try:
            if self._game_detected_at is not None and self._playing_lbl is not None:
                secs = int((datetime.now() - self._game_detected_at).total_seconds())
                mins = secs // 60
                rem = secs % 60
                self._playing_lbl.config(text=f"Playing for: {mins}m {rem}s",
                                         fg=TEXT_DIM)
                self._after_playing_id = self.after(1000, self._update_playing_timer)
            else:
                self._after_playing_id = None
        except Exception:
            self._after_playing_id = None

    # ── Feature D: Parental control quick-lock ─────────────────────────────────

    def _open_family_lock(self):
        """Open the Quick Lock dialog (educational / visual feature only)."""
        if self._family_lock_active:
            messagebox.showinfo(
                "Family Lock",
                "A family lock is already active. It will lift automatically.")
            return

        win = tk.Toplevel(self)
        win.title("Quick Lock")
        win.configure(bg=BG_BASE)
        win.resizable(False, False)
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass

        tk.Label(win, text="🔐  Family Lock", bg=BG_BASE, fg=TEXT_MAIN,
                 font=F_HEAD).pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(win, text="Lock Steam for [X] minutes?", bg=BG_BASE, fg=TEXT_DIM,
                 font=F_BODY).pack(anchor="w", padx=18)

        spin_row = tk.Frame(win, bg=BG_BASE)
        spin_row.pack(fill="x", padx=18, pady=(12, 4))
        tk.Label(spin_row, text="Minutes:", bg=BG_BASE, fg=TEXT_DIM,
                 font=F_SMALL).pack(side="left")
        minutes_var = tk.IntVar(value=30)
        spin = tk.Spinbox(spin_row, from_=5, to=120, increment=5,
                          textvariable=minutes_var, width=6,
                          bg=BG_CARD, fg=TEXT_MAIN, font=F_BODY,
                          buttonbackground=BG_ELEVATED, relief="flat",
                          insertbackground=TEXT_MAIN, justify="center")
        spin.pack(side="left", padx=(8, 0))

        btn_row = tk.Frame(win, bg=BG_BASE)
        btn_row.pack(fill="x", padx=18, pady=(14, 16))

        def _confirm():
            try:
                mins = int(minutes_var.get())
            except Exception:
                mins = 30
            mins = max(5, min(120, mins))
            self._activate_family_lock(mins)
            try:
                win.destroy()
            except Exception:
                pass

        lock_btn = tk.Button(btn_row, text="🔒  Lock", bg=RED, fg="white",
                             font=("Segoe UI", 10, "bold"), relief="flat", bd=0,
                             cursor="hand2", activebackground=BG_ELEVATED,
                             command=_confirm)
        lock_btn.pack(side="right", ipadx=10, ipady=5)
        cancel_btn = tk.Button(btn_row, text="Cancel", bg=BG_CARD, fg=TEXT_DIM,
                               font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                               activebackground=BG_ELEVATED,
                               command=win.destroy)
        cancel_btn.pack(side="right", ipadx=8, ipady=5, padx=(0, 8))

        win.update_idletasks()
        try:
            px, py = self.winfo_x(), self.winfo_y()
            pw, ph = self.winfo_width(), self.winfo_height()
            ww, wh = win.winfo_width(), win.winfo_height()
            win.geometry(f"+{px + (pw - ww)//2}+{py + (ph - wh)//2}")
        except Exception:
            pass

    def _activate_family_lock(self, minutes: int):
        """Engage the (educational) family lock for the given minutes."""
        self._family_lock_active = True
        self._log(f"Family lock: Steam restricted for {minutes} minutes",
                  level="info")
        if self._family_lock_btn is not None:
            try:
                self._family_lock_btn.config(bg=RED, fg="white")
            except Exception:
                pass
        if self._family_lock_after_id is not None:
            try:
                self.after_cancel(self._family_lock_after_id)
            except Exception:
                pass
        self._family_lock_after_id = self.after(
            minutes * 60000, self._unlock_family_lock)

    def _unlock_family_lock(self):
        """Lift the family lock and restore the button."""
        self._family_lock_active = False
        self._family_lock_after_id = None
        if self._family_lock_btn is not None:
            try:
                self._family_lock_btn.config(bg=BG_CARD, fg=TEXT_DIM)
            except Exception:
                pass
        self._log("Family lock lifted", level="info")

    def _rescan_games(self):
        def worker():
            self.after(0, lambda: self._log("Re-scanning Steam library…"))
            if self._steam_dir:
                self._catalog = build_game_catalog(self._steam_dir)
                count = len(self._catalog)
                self.after(0, lambda: self._log(f"Scan complete — {count} games found."))
            else:
                self.after(0, lambda: self._log("Steam not found.", True))
        threading.Thread(target=worker, daemon=True).start()

    # ── Rule self-heal monitor ────────────────────────────────────────────────

    def _rule_health_loop(self):
        """
        Background thread — checks the firewall rule every 30 s.

        While the app is running it ensures the rule always exists (disabled
        or enabled as appropriate).  If protection is active and the rule has
        been removed or disabled by Windows Security or another tool, it
        silently recreates/re-enables it and logs a RULE SELF-HEAL event.
        """
        time.sleep(15)  # grace period — let startup finish first
        while self._app_running:
            time.sleep(30)
            if not self._steam_exe:
                continue
            try:
                state = fw_rule_state()
                if state is None:
                    # Rule was deleted — recreate it
                    ok, _msg = fw_create(self._steam_exe)
                    if ok:
                        if self._protected:
                            fw_enable_fast()
                            action = "recreated & re-enabled"  # local var, not late-bound
                            self.after(0, lambda a=action: self._rule_healed(a))
                        else:
                            self.after(0, lambda: self._sync_fw_state(False))
                            self.after(0, lambda: self._log(
                                "Rule self-heal: rule was deleted — recreated (inactive)."))
                elif state is False and self._protected:
                    # Rule unexpectedly disabled while protecting
                    fw_enable_fast()
                    self.after(0, lambda: self._rule_healed("re-enabled"))
            except Exception:
                pass

    def _rule_healed(self, action: str):
        """Called on the main thread after a successful rule self-heal."""
        self._rule_heal_cnt += 1
        self._last_heal_time = datetime.now()
        self._sync_fw_state(True)
        self._log(
            f"RULE SELF-HEAL #{self._rule_heal_cnt}: rule was {action} automatically.",
            level="heal")
        self._update_heal_badge()

    def _connection_kill_loop(self):
        """
        Background thread — tight 200 ms poll while protection is active.

        Steam opens 6-8 CM connections simultaneously and retries within
        seconds.  A 2-second sweep is too slow — the lock message arrives
        in under a second.  This loop runs every 200 ms to RST any Valve
        TCP connection before Steam can receive a library-lock message.

        _kill_valve_connections() no longer sleeps internally when called
        from here — the loop cadence itself provides the pacing.
        """
        while self._app_running:
            if not self._protected:
                time.sleep(0.5)
                continue
            try:
                killed = _kill_valve_connections(sleep_after=False)
                if killed:
                    self.after(0, lambda n=killed: self._log(
                        f"Connection watchdog: severed {n} Valve connection(s).",
                        level="kill"))
                    self.after(0, lambda n=killed: self._increment_kill_counter(n))
            except Exception:
                pass
            time.sleep(0.2)

    # ── Autostart toggle ──────────────────────────────────────────────────────

    def _on_autostart_toggle(self):
        ok = set_autostart(self._autostart_var.get())
        if ok:
            status = "enabled" if self._autostart_var.get() else "disabled"
            self._log(f"Start with Windows: {status}.")
            self._save_settings()
        else:
            self._log("Could not update Windows startup registry.", True)
            self._autostart_var.set(not self._autostart_var.get())  # revert

    # ── Help dialog ───────────────────────────────────────────────────────────

    def _show_help(self):
        win = tk.Toplevel(self)
        win.title("How to Use SteamGuard")
        win.configure(bg=BG_DARK)
        win.resizable(False, False)
        win.grab_set()

        # Center relative to parent
        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width()  - 520) // 2
        y = self.winfo_y() + (self.winfo_height() - 520) // 2
        win.geometry(f"520x520+{x}+{y}")

        tk.Label(win, text="How to Use SteamGuard", bg=BG_DARK, fg=TEXT_MAIN,
                 font=("Segoe UI", 12, "bold")).pack(pady=(16, 0))
        tk.Label(win, text="Steam Family Sharing — keep playing when the owner logs in",
                 bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL).pack()

        txt = scrolledtext.ScrolledText(
            win, bg=BG_PANEL, fg=TEXT_MAIN, font=("Segoe UI", 9),
            relief="flat", bd=0, wrap="word", state="normal",
            padx=14, pady=10)
        txt.pack(fill="both", expand=True, padx=12, pady=(12, 0))

        GUIDE = """\
QUICK START  (30 seconds)
─────────────────────────
IMPORTANT — order matters:

1. Run SteamGuard.bat as Administrator (it prompts UAC automatically).
2. YOU launch Police Simulator (or any shared game) from your Steam.
3. Once the game is loading/running, click START PROTECTION in SteamGuard.
   (SteamGuard may auto-click it for you if "Auto-protect" is checked.)
4. NOW tell your friend to launch the game on his end.
5. Both of you are in — Steam on your side can't report the library lock.

WHY this order? Steam registers "game in use" with Valve the moment you
launch.  If your friend tries to launch BEFORE you block, Valve's server
already knows the game is in use and sends the lock to your friend.
Block FIRST, friend launches SECOND — Valve never gets the in-use signal.

WHAT IT DOES
────────────
SteamGuard blocks Steam.exe from reaching Valve's CM servers entirely.
This prevents Steam from registering that you launched a game, so the
library-lock signal is never sent to your friend's machine.

  What IS blocked: Steam client ↔ Valve CM servers (all traffic)
  What is NOT blocked: The game's own servers, Epic Online Services,
    direct game UDP traffic to non-Valve IPs

LIMITATION: While protection is ON, Steam-based multiplayer features
(Steam voice chat, Steam invites, Steam friend joining) won't work
because they also route through CM servers.  Workaround: use Discord
for voice/invites, join each other's game session directly in-game.

STATUS INDICATORS
─────────────────
  FIREWALL
    NO RULE   – rule hasn't been created yet (happens on first run)
    INACTIVE  – rule exists but is disabled (not protected)
    BLOCKING  – rule is active, CM traffic is blocked ✓

  AUTO-HEAL
    Shows how many times the network heal fired.  Each time your
    internet reconnects the block is re-applied before Steam reacts.

  NETWORK
    Online / Offline — current internet connectivity.

AUTO-LAUNCH WITH ANY STEAM GAME
────────────────────────────────
Option A — Start with Windows (recommended):
  Tick "Start with Windows" in SteamGuard.  It runs silently in the
  background.  Whenever you launch a Steam game, protection activates
  automatically (requires "Auto-protect when game launches" to be on).

Option B — Steam Launch Options (per-game):
  In Steam, right-click a game → Properties → Launch Options and paste:

    python "<PATH_TO_STEAMGUARD>" & %command%

  (Click "Steam Integration" for the exact copy-pasteable command.)
  This starts SteamGuard alongside the game every time you hit Play.

TIPS
────
• Keep "Auto-protect when game launches" checked for fully automatic use.
• The firewall rule is disabled automatically when you stop playing.
• If you see "Could not create rule", make sure you ran as Administrator.
• SteamGuard targets only Valve CM IPs — your game's own servers are
  never blocked (multiplayer, achievements, and cloud saves work normally
  as long as they do not route through CM servers).

KEYBOARD SHORTCUTS
──────────────────
  Ctrl+P   Toggle protection on/off
  Ctrl+L   Clear event log
  Ctrl+E   Export log to file
  Ctrl+S   Re-scan Steam library
  F1       Show this help
  Escape   Minimize window
"""
        txt.insert("1.0", GUIDE)
        txt.config(state="disabled")

        tk.Button(win, text="Close", bg=ACCENT, fg="white",
                  font=F_BODY, relief="flat", bd=0, cursor="hand2",
                  activebackground="#1c6cc4",
                  command=win.destroy).pack(pady=12, ipady=6, ipadx=24)

    # ── Steam Integration dialog ──────────────────────────────────────────────

    def _show_steam_integration(self):
        """Shows step-by-step instructions for per-game Steam launch options."""
        win = tk.Toplevel(self)
        win.title("Steam Game Integration")
        win.configure(bg=BG_DARK)
        win.resizable(False, False)
        win.grab_set()

        self.update_idletasks()
        x = self.winfo_x() + (self.winfo_width()  - 500) // 2
        y = self.winfo_y() + (self.winfo_height() - 400) // 2
        win.geometry(f"500x400+{x}+{y}")

        tk.Label(win, text="Launch SteamGuard with a Steam Game",
                 bg=BG_DARK, fg=TEXT_MAIN,
                 font=("Segoe UI", 12, "bold")).pack(pady=(16, 4))
        tk.Label(win,
                 text="Set this up once per game — protection starts automatically.",
                 bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL).pack()

        frm = tk.Frame(win, bg=BG_CARD)
        frm.pack(fill="both", expand=True, padx=12, pady=10)

        script_path = os.path.abspath(__file__).replace("\\", "\\\\")
        launch_cmd  = f'python "{script_path}" & %command%'

        steps = [
            ("Step 1", "Open Steam and go to your Library."),
            ("Step 2", "Right-click the game you want to protect → Properties."),
            ("Step 3", "Click the  General  tab → find  Launch Options."),
            ("Step 4", "Paste the command below into the Launch Options box:"),
        ]

        for title, desc in steps:
            row = tk.Frame(frm, bg=BG_CARD)
            row.pack(fill="x", padx=12, pady=(8, 0))
            tk.Label(row, text=title, bg=BG_CARD, fg=ACCENT,
                     font=("Segoe UI", 9, "bold")).pack(side="left")
            tk.Label(row, text=f"  {desc}", bg=BG_CARD, fg=TEXT_MAIN,
                     font=("Segoe UI", 9), wraplength=380, justify="left",
                     anchor="w").pack(side="left", fill="x")

        # Copyable command box
        cmd_var = tk.StringVar(value=launch_cmd)
        cmd_entry = tk.Entry(frm, textvariable=cmd_var,
                             bg=BG_PANEL, fg=GREEN, font=F_MONO,
                             relief="flat", bd=0, readonlybackground=BG_PANEL)
        cmd_entry.pack(fill="x", padx=12, pady=(6, 0), ipady=6)
        cmd_entry.config(state="readonly")

        def copy_cmd():
            self.clipboard_clear()
            self.clipboard_append(launch_cmd)
            copy_btn.config(text="Copied!", fg=GREEN)
            win.after(2000, lambda: copy_btn.config(text="Copy Command", fg=TEXT_MAIN))

        copy_btn = tk.Button(frm, text="Copy Command", bg=BG_CARD,
                             fg=TEXT_MAIN, font=F_SMALL, relief="flat", bd=0,
                             cursor="hand2", activebackground=BORDER,
                             command=copy_cmd)
        copy_btn.pack(pady=(6, 0), ipady=4, ipadx=10)

        tk.Label(frm,
                 text="After pasting, click OK.  Next time you hit Play in Steam,\n"
                      "SteamGuard opens first then the game launches automatically.",
                 bg=BG_CARD, fg=TEXT_DIM, font=("Segoe UI", 8),
                 justify="center").pack(pady=(10, 4))

        tk.Button(win, text="Done", bg=ACCENT, fg="white",
                  font=F_BODY, relief="flat", bd=0, cursor="hand2",
                  activebackground="#1c6cc4",
                  command=win.destroy).pack(pady=10, ipady=6, ipadx=24)

    # ── System tray ───────────────────────────────────────────────────────────

    def _try_start_tray(self):
        """
        Attempt to create a system tray icon using pystray.
        Silently skipped if pystray / Pillow are not installed.
        The tray icon lets the user minimise SteamGuard without a taskbar button.
        """
        try:
            import pystray
            from PIL import Image, ImageDraw

            # Draw a simple shield icon in memory
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d   = ImageDraw.Draw(img)
            # Shield polygon
            d.polygon([(32, 4), (60, 16), (60, 36), (32, 60), (4, 36), (4, 16)],
                      fill="#58a6ff")
            d.text((22, 24), "S", fill="white")

            def on_show(icon, item):
                icon.stop()
                self._tray_icon = None
                self._minimized_to_tray = False
                self.after(0, self.deiconify)

            def on_quit(icon, item):
                icon.stop()
                self._tray_icon = None
                self.after(0, self._on_close)

            menu = pystray.Menu(
                pystray.MenuItem("Show SteamGuard", on_show, default=True),
                pystray.MenuItem("Quit",            on_quit),
            )
            self._tray_icon = pystray.Icon(
                "SteamGuard", img, "SteamGuard", menu)

            # Minimize to tray: hide window when iconified
            self.bind("<Unmap>", self._on_minimize)
        except Exception:
            pass  # pystray/Pillow not installed — no tray icon, normal taskbar behaviour

    def _on_minimize(self, event):
        """Hide to tray when minimized (only if tray icon is running)."""
        if self._tray_icon and not self._minimized_to_tray:
            self._minimized_to_tray = True
            self.withdraw()
            threading.Thread(
                target=self._tray_icon.run, daemon=True).start()

    # ── Log ───────────────────────────────────────────────────────────────────

    _LOG_MAX_LINES = 500

    def _log(self, text: str, error: bool = False, color: str | None = None, level: str = "info"):
        log_to_file(text)   # write to %APPDATA%\SteamGuard\steamguard.log
        ts = datetime.now().strftime("%H:%M:%S")

        # Level color mapping
        _level_colors = {
            "heal":    PURPLE,
            "warn":    YELLOW,
            "error":   RED,
            "kill":    YELLOW,
            "success": GREEN,
        }

        if color:
            c = color
        elif error:
            c = RED
        else:
            c = _level_colors.get(level, TEXT_DIM)

        w  = self._log_w
        w.config(state="normal")

        # Trim to keep memory bounded — drop oldest 100 lines when over limit
        total = int(w.index("end-1c").split(".")[0])
        if total > self._LOG_MAX_LINES:
            w.delete("1.0", f"{total - self._LOG_MAX_LINES + 100}.0")

        w.insert("end", f"[{ts}]  {text}\n")
        ln  = int(w.index("end-1c").split(".")[0])
        tag = f"t{ln}"
        w.tag_add(tag, f"{ln}.0", f"{ln}.end")
        w.tag_config(tag, foreground=c)
        w.see("end")
        w.config(state="disabled")

    # ── Close ─────────────────────────────────────────────────────────────────

    def _on_close(self):
        self._app_running = False   # stop rule health loop
        # Cancel the stats ticker so it doesn't fire after destroy()
        try:
            self.after_cancel(self._after_stats_id)
        except Exception:
            pass
        # Stop tray icon if running
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        # Close rich presence
        if self._rpc is not None:
            try:
                self._rpc.close()
            except Exception:
                pass
        self._monitor.stop()
        if self._protected and self._fw_state:
            fw_disable_fast()
        _close_log_file()
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── 0. Update Check (First thing on startup) ─────────────────────────────
    check_for_updates()

    # ── 1. Anti-tamper checks (before any window opens) ──────────────────────
    try:
        from auth.guard import run_checks
        run_checks()
    except SystemExit:
        raise
    except Exception:
        pass   # guard import failed (dev mode without auth/) — continue

    # ── 2. UAC elevation ──────────────────────────────────────────────────────
    if not is_admin():
        elevate()

    # ── 3. TOS + License preflight ───────────────────────────────────────────
    _session = None
    try:
        from auth.screens import run_preflight
        _session = run_preflight()
    except SystemExit:
        raise   # user declined TOS / closed activation window
    except ImportError:
        pass    # auth/ not present (dev/testing mode)
    except Exception as e:
        import tkinter.messagebox as _mb
        import tkinter as _tk
        _r = _tk.Tk(); _r.withdraw()
        _mb.showerror("SteamGuard", f"License check failed:\n{e}")
        _r.destroy()
        raise SystemExit(1)

    # ── 4. Launch main app ────────────────────────────────────────────────────
    app = SteamGuard()
    # Background daily re-verify loop
    if _session:
        def _daily_reverify():
            import time as _time
            from auth.cache  import session_needs_refresh, save_session
            from auth.client import verify
            from pathlib import Path
            import json, os
            cfg_path = Path(os.environ.get("APPDATA","")) / "SteamGuard" / "config.json"
            while getattr(app, "_app_running", False):
                _time.sleep(300)   # check every 5 min if refresh is due
                if not getattr(app, "_app_running", False):
                    break
                if session_needs_refresh():
                    try:
                        cfg = json.loads(cfg_path.read_text("utf-8")) if cfg_path.exists() else {}
                        k   = cfg.get("license_key", "")
                        did = cfg.get("discord_user_id", "")
                        if k and did:
                            r = verify(k, did)
                            if r.ok:
                                save_session(r.session_token, r.token_expires, k, did)
                            else:
                                # Membership lost — shut down gracefully
                                app.after(0, lambda msg=r.error: (
                                    __import__("tkinter.messagebox",
                                               fromlist=["showerror"]).showerror(
                                        "SteamGuard — Access Revoked", msg),
                                    app._on_close()))
                                break
                    except Exception:
                        pass
        import threading as _threading
        _threading.Thread(target=_daily_reverify, daemon=True).start()

    app.mainloop()
