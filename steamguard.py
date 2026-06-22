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
from datetime import datetime, timedelta
from ipaddress import ip_address, ip_network
from pathlib import Path
import tkinter as tk
from tkinter import messagebox
import urllib.request
import webbrowser
import sys

CURRENT_VERSION = "1.0.0"

def check_for_updates():
    """Checks GitHub on startup and forces user to get the update via Linkvertise if outdated."""
    try:
        url = "https://raw.githubusercontent.com/rivvak/SteamGuard/main/version.txt"
        with urllib.request.urlopen(url, timeout=4) as resp:
            latest_version = resp.read().decode("utf-8").strip()
            
        if latest_version != CURRENT_VERSION:
            messagebox.showwarning(
                "Update Required",
                f"A new version of SteamGuard (v{latest_version}) is available!\n\n"
                "We are redirecting you to get the latest update now."
            )
            # Redirect them to your linkvertise link to download the update
            webbrowser.open("https://linkvertise.com/YOUR_LINK_HERE")
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

BG_DARK   = "#0d1117"
BG_MID    = "#161b22"
BG_PANEL  = "#1c2128"
BG_CARD   = "#21262d"
ACCENT    = "#58a6ff"
GREEN     = "#3fb950"
RED       = "#f85149"
YELLOW    = "#d29922"
PURPLE    = "#bc8cff"
TEXT_MAIN = "#e6edf3"
TEXT_DIM  = "#8b949e"
BORDER    = "#30363d"

F_HEAD  = ("Segoe UI", 13, "bold")
F_BODY  = ("Segoe UI", 10)
F_SMALL = ("Segoe UI", 9)
F_MONO  = ("Consolas", 9)

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
# Main Application
# ─────────────────────────────────────────────────────────────────────────────

class SteamGuard(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("SteamGuard")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)

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

        # Trace auto-protect changes → persist
        self._auto_heal.trace_add("write", lambda *_: self._save_settings())

        self._monitor = NetworkMonitor(self._on_cm_detected)

        # Animation state
        self._pulse_rings: list[dict] = []   # active expanding rings
        self._shield_glow  = 0.0             # 0.0–1.0 glow intensity
        self._shield_dir   = 1               # +1 brightening / -1 dimming
        self._anim_running = False

        self._build_ui()
        self._initial_load()

        self.update_idletasks()
        w, h = 520, 740
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tray_icon = None
        self._minimized_to_tray = False
        self._try_start_tray()

    def _save_settings(self):
        cfg = {
            "auto_protect": self._auto_heal.get(),
            "autostart":    self._autostart_var.get(),
        }
        save_config(cfg)

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
            self.after(0, lambda: self._log("Network monitor active (4 layers)."))
            # Start game detection loop
            self.after(2000, self._detect_game_loop)
            # Start rule self-heal monitor
            threading.Thread(target=self._rule_health_loop, daemon=True).start()
            # Start connection kill loop — proactively RSTs any Valve TCP
            # connections that form while protection is active
            threading.Thread(target=self._connection_kill_loop, daemon=True).start()

        threading.Thread(target=worker, daemon=True).start()

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
        self._log(msg, color=PURPLE)
        self._sync_fw_state(True)
        self._update_heal_badge()

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
                self.after(0, lambda n=killed: self._log(
                    f"Severed {n} live Valve CM connection(s) — Steam now offline.",
                    color=YELLOW) if n else None)
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
                      color=GREEN)
            _play_protect_sound()
            _show_toast("SteamGuard — Protected",
                        f"Library lock blocked for {game_name}. Tell your friend to launch now.")
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
        # ── Header bar ────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG_PANEL, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # Small shield icon in header
        hc = tk.Canvas(hdr, width=28, height=28, bg=BG_PANEL, highlightthickness=0)
        hc.place(x=14, y=12)
        hc.create_polygon(14,2, 26,7, 26,16, 14,26, 2,16, 2,7,
                          fill=ACCENT, outline="", smooth=False)
        hc.create_text(14,15, text="S", fill="white", font=("Segoe UI",9,"bold"))

        tk.Label(hdr, text="SteamGuard", bg=BG_PANEL, fg=TEXT_MAIN,
                 font=("Segoe UI", 13, "bold")).place(x=48, y=8)
        tk.Label(hdr, text="Family Sharing bypass",
                 bg=BG_PANEL, fg=TEXT_DIM, font=F_SMALL).place(x=48, y=29)

        badge_text  = "✓ Admin" if self._admin else "⚠ No Admin"
        badge_color = GREEN if self._admin else YELLOW
        tk.Label(hdr, text=badge_text, bg=BG_PANEL, fg=badge_color,
                 font=F_SMALL).place(relx=1.0, x=-14, y=18, anchor="ne")

        # ── Animated shield canvas (centrepiece) ──────────────────────────────
        SHIELD_W, SHIELD_H = 520, 160
        self._shield_cv = tk.Canvas(self, width=SHIELD_W, height=SHIELD_H,
                                    bg=BG_DARK, highlightthickness=0)
        self._shield_cv.pack(fill="x")

        cx, cy = SHIELD_W // 2, SHIELD_H // 2 + 4

        # Pulse rings (drawn behind shield, updated by animation)
        self._ring_ids = [
            self._shield_cv.create_oval(0,0,0,0, outline=GREEN, width=2, state="hidden")
            for _ in range(3)
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
            fill="#1a3a5c", outline=ACCENT, width=2, smooth=False)
        self._shield_letter = self._shield_cv.create_text(
            cx, cy+2, text="S", fill=ACCENT,
            font=("Segoe UI", 28, "bold"))

        # Status text under shield
        self._shield_status_txt = self._shield_cv.create_text(
            cx, cy + SH//2 + 16, text="UNPROTECTED",
            fill=TEXT_DIM, font=("Segoe UI", 9, "bold"))

        # ── 3-column status indicators ────────────────────────────────────────
        status_outer = tk.Frame(self, bg=BG_DARK)
        status_outer.pack(fill="x", padx=12, pady=(0, 0))

        def _make_indicator(parent, label):
            card = tk.Frame(parent, bg=BG_CARD)
            card.pack(side="left", fill="both", expand=True, padx=(0,4))
            tk.Label(card, text=label, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", 7, "bold")).pack(pady=(7,1))
            dot_cv = tk.Canvas(card, width=10, height=10, bg=BG_CARD,
                               highlightthickness=0)
            dot_cv.pack()
            dot = dot_cv.create_oval(1,1,9,9, fill=TEXT_DIM, outline="")
            lbl = tk.Label(card, text="—", bg=BG_CARD, fg=TEXT_DIM,
                           font=("Segoe UI", 8, "bold"))
            lbl.pack(pady=(1,7))
            return dot_cv, dot, lbl

        self._fw_dot_cv,   self._fw_dot,   self._fw_lbl   = _make_indicator(status_outer, "FIREWALL")
        self._heal_dot_cv, self._heal_dot, self._heal_count_lbl = _make_indicator(status_outer, "AUTO-HEAL")
        self._net_dot_cv,  self._net_dot,  self._net_lbl  = _make_indicator(status_outer, "NETWORK")
        # Fix last card: no right padding
        status_outer.winfo_children()[-1].pack_configure(padx=0)

        # ── Game detection card ───────────────────────────────────────────────
        game_section = tk.Frame(self, bg=BG_DARK)
        game_section.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(game_section, text="DETECTED GAME", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(0,3))

        self._game_card = tk.Frame(game_section, bg=BG_CARD)
        self._game_card.pack(fill="x")

        self._game_icon = tk.Label(self._game_card, text="🎮", bg=BG_CARD,
                                   font=("Segoe UI Emoji", 18))
        self._game_icon.pack(side="left", padx=(12,8), pady=10)

        game_text = tk.Frame(self._game_card, bg=BG_CARD)
        game_text.pack(side="left", fill="x", expand=True, pady=10)
        self._game_name_lbl = tk.Label(game_text, text="Waiting for game to launch…",
                                       bg=BG_CARD, fg=TEXT_DIM, font=F_BODY, anchor="w")
        self._game_name_lbl.pack(fill="x")
        self._game_meta_lbl = tk.Label(game_text, text="Launch a Steam game to begin",
                                       bg=BG_CARD, fg=TEXT_DIM, font=F_SMALL, anchor="w")
        self._game_meta_lbl.pack(fill="x")

        self._game_badge = tk.Label(self._game_card, text="", bg=BG_CARD,
                                    fg=TEXT_DIM, font=("Segoe UI", 7, "bold"))
        self._game_badge.pack(side="right", padx=10)

        # ── Big protect button ────────────────────────────────────────────────
        self._protect_btn = tk.Button(
            self, text="🛡  START PROTECTION",
            bg=ACCENT, fg="white",
            font=("Segoe UI", 12, "bold"),
            relief="flat", bd=0, cursor="hand2",
            activebackground="#1c6cc4",
            command=self._on_protect_toggle)
        self._protect_btn.pack(fill="x", padx=12, pady=(10, 0), ipady=13)

        # ── Session stats strip ───────────────────────────────────────────────
        stats_row = tk.Frame(self, bg=BG_DARK)
        stats_row.pack(fill="x", padx=12, pady=(4, 0))
        self._stats_lbl = tk.Label(
            stats_row, text="Session: 0s  |  Heals: 0  |  Last heal: —",
            bg=BG_DARK, fg=TEXT_DIM, font=("Segoe UI", 8), anchor="w")
        self._stats_lbl.pack(side="left")
        self._after_stats_id = self.after(1000, self._update_stats_strip)

        # ── Options rows ──────────────────────────────────────────────────────
        opts1 = tk.Frame(self, bg=BG_DARK)
        opts1.pack(fill="x", padx=12, pady=(6, 0))
        tk.Checkbutton(opts1, text="Auto-protect when game launches",
                       variable=self._auto_heal,
                       bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL,
                       selectcolor=BG_CARD, activebackground=BG_DARK,
                       activeforeground=TEXT_MAIN,
                       highlightthickness=0).pack(side="left")
        tk.Button(opts1, text="Scan games", bg=BG_CARD, fg=TEXT_DIM,
                  font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                  activebackground=BORDER,
                  command=self._rescan_games).pack(side="right", ipady=4, ipadx=8)

        opts2 = tk.Frame(self, bg=BG_DARK)
        opts2.pack(fill="x", padx=12, pady=(3, 0))
        tk.Checkbutton(opts2, text="Start with Windows",
                       variable=self._autostart_var,
                       bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL,
                       selectcolor=BG_CARD, activebackground=BG_DARK,
                       activeforeground=TEXT_MAIN, highlightthickness=0,
                       command=self._on_autostart_toggle).pack(side="left")
        tk.Button(opts2, text="? Help", bg=BG_CARD, fg=ACCENT,
                  font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                  activebackground=BORDER,
                  command=self._show_help).pack(side="right", ipady=4, ipadx=8)
        tk.Button(opts2, text="Steam Integration", bg=BG_CARD, fg=TEXT_DIM,
                  font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                  activebackground=BORDER,
                  command=self._show_steam_integration
                  ).pack(side="right", ipady=4, ipadx=8, padx=(0,4))

        # ── Log ───────────────────────────────────────────────────────────────
        log_outer = tk.Frame(self, bg=BG_DARK)
        log_outer.pack(fill="both", expand=True, padx=12, pady=(8, 8))
        tk.Label(log_outer, text="EVENT LOG", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(0,3))
        self._log_w = scrolledtext.ScrolledText(
            log_outer, bg=BG_PANEL, fg=TEXT_DIM, font=F_MONO,
            relief="flat", bd=0, state="disabled", wrap="word")
        self._log_w.pack(fill="both", expand=True)

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
                                       fill="#0d2a0d", outline=GREEN)
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
                                       fill="#1a3a5c", outline=ACCENT)
            self._shield_cv.itemconfig(self._shield_letter, fill=ACCENT)
            self._shield_cv.itemconfig(self._shield_status_txt,
                                       text="UNPROTECTED", fill=TEXT_DIM)
            for rid in self._ring_ids:
                self._shield_cv.itemconfig(rid, state="hidden")

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

    def _update_game_card(self, game: dict | None):
        if game:
            self._game_name_lbl.config(
                text=game["name"], fg=TEXT_MAIN)
            self._game_meta_lbl.config(
                text=f"AppID {game['appid']}  •  detected via {game.get('method','?')}",
                fg=TEXT_DIM)
            self._game_badge.config(text="● ACTIVE", fg=GREEN)
            self._game_icon.config(text="🎮")
        else:
            self._game_name_lbl.config(
                text="No game running", fg=TEXT_DIM)
            self._game_meta_lbl.config(
                text="Launch a Steam game to begin", fg=TEXT_DIM)
            self._game_badge.config(text="", fg=TEXT_DIM)

    def _anim_tick(self):
        """
        Animation tick — runs every 50 ms while protection is active.

        Two effects:
        1. Shield glow: the outline colour brightness pulses smoothly
           between dim-green and bright-green using a sine wave.
        2. Pulse rings: three concentric ovals expand outward from the
           shield and fade out, staggered 800 ms apart, looping every
           2.4 s.
        """
        if not self._anim_running or not self._app_running:
            return

        import math, time as _time

        SHIELD_W, SHIELD_H = 520, 160
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

        for i, rid in enumerate(self._ring_ids):
            phase = ((t + i * (RING_PERIOD / len(self._ring_ids)))
                     % RING_PERIOD) / RING_PERIOD     # 0.0 – 1.0
            rx = BASE_R_X + phase * RING_MAX_R
            ry = BASE_R_Y + phase * RING_MAX_R
            alpha = max(0.0, 1.0 - phase * 1.6)       # fade out by ~62%
            green_v = int(alpha * 0xaa)
            col = f"#00{green_v:02x}00"
            self._shield_cv.coords(rid,
                cx - rx, cy - ry, cx + rx, cy + ry)
            self._shield_cv.itemconfig(rid,
                outline=col, state="normal" if alpha > 0.02 else "hidden")

        self.after(50, self._anim_tick)

    def _update_protect_btn(self):
        if self._protected:
            self._protect_btn.config(
                state="normal",
                text="🛡  PROTECTION ON — Click to Stop",
                bg="#1a6b1a", activebackground="#145214")
        else:
            self._protect_btn.config(
                state="normal",
                text="🛡  START PROTECTION",
                bg=ACCENT, activebackground="#1c6cc4")

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
            color=PURPLE)
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
                        color=YELLOW))
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

    def _log(self, text: str, error: bool = False, color: str | None = None):
        log_to_file(text)   # write to %APPDATA%\SteamGuard\steamguard.log
        ts = datetime.now().strftime("%H:%M:%S")
        c  = color or (RED if error else TEXT_DIM)
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
