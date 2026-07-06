"""
SteamGuard — Universal Steam Sharing Bypass  (PySide6 UI)
=========================================================
PySide6 rewrite of the SteamGuard desktop UI. ALL business logic
(firewall management, network monitoring, game detection, HMAC heartbeat,
license handling, self-healing) is preserved verbatim from steamguard.py —
only the UI layer (tkinter → PySide6) has been replaced.

Dark gaming aesthetic: Discord-meets-gaming-overlay.

HOW IT WORKS (unchanged from the original):
  Steam's library lock is enforced by Valve's Connection Manager (CM) servers
  over TCP 443. This tool creates a targeted Windows Firewall rule blocking
  Steam.exe → Valve CM IPs, monitors the network for reconnect events, and
  re-applies the block within ~200ms of detecting reconnection. It auto-detects
  the running Steam game and shows live status.

REQUIREMENTS:
  - Windows 10/11, Python 3.10+, run as Administrator
  - pip install PySide6 psutil wmi pywin32  (wmi/pywin32 optional)
"""

import sys
import os
import re
import time
import json
import glob
import socket
import struct
import ctypes
import threading
import subprocess
import urllib.request
import webbrowser
from datetime import datetime
from ipaddress import ip_address, ip_network
from pathlib import Path

# winreg / winsound are Windows-only stdlib modules. Guard the imports so the
# file can at least be imported / compiled on non-Windows for development.
try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows dev
    winreg = None
try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows dev
    winsound = None

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QStackedWidget, QTextEdit, QButtonGroup,
    QSizePolicy, QGraphicsOpacityEffect, QGraphicsDropShadowEffect, QMessageBox,
)
from PySide6.QtCore import (
    Qt, QTimer, QObject, Signal, QThread, QPropertyAnimation,
    QEasingCurve, QPoint, QRectF,
)
from PySide6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QTextCursor, QTextCharFormat,
    QIcon, QPixmap, QFontDatabase, QPainterPath,
)

# Pull in the steam_features helpers exactly like the original UI did. These
# are pure-logic (game classification / appid registry helpers). Best-effort
# so the module still imports if steam_features is unavailable in dev.
try:
    from steam_features import classify_game, get_running_appid_reg  # noqa: F401
except Exception:  # pragma: no cover
    classify_game = None
    get_running_appid_reg = None

CURRENT_VERSION = "1.4.0"

# ─────────────────────────────────────────────────────────────────────────────
# DESIGN SYSTEM — dark gaming palette
# ─────────────────────────────────────────────────────────────────────────────

# Backgrounds
C_BG        = "#0B0D10"      # match loader base
C_SURFACE   = "#0F1114"      # match loader card surface
C_CARD      = "#12161C"      # slightly lighter than surface for depth
C_BORDER    = "#1F2937"      # subtle borders

# Accent (ImGui blue — match loader exactly)
C_ACCENT       = "#3B82F6"   # primary blue
C_ACCENT_HOVER = "#60A5FA"   # lighter for hover
C_ACCENT_GLOW  = QColor(66, 150, 250, 180)  # same glow as loader RC logo

# Semantic
C_SUCCESS   = "#22C55E"      # green ON state
C_WARNING   = "#F59E0B"      # amber
C_ERROR     = "#EF4444"      # red OFF state
C_ERROR_HOVER = "#DC2626"

# Text
C_TEXT      = "#F8FAFC"      # primary text (match loader TEXT)
C_TEXT_DIM  = "#94A3B8"      # muted secondary
C_TEXT_MUTED = "#64748B"     # labels

# Glassmorphism system — matches loader.py commit 9865c7a
GLASS_BG      = "rgba(15, 17, 20, 0.72)"
GLASS_BORDER  = "rgba(255, 255, 255, 0.08)"
GLASS_HILITE  = "rgba(255, 255, 255, 0.05)"
BG_DEEP       = "#050708"

# ─────────────────────────────────────────────────────────────────────────────
# App data paths
# ─────────────────────────────────────────────────────────────────────────────

_APPDATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "SteamGuard"
_CONFIG_FILE = _APPDATA_DIR / "config.json"
_LOG_FILE    = _APPDATA_DIR / "steamguard.log"
_DEBUG_LOG_FILE = _APPDATA_DIR / "steamguard_debug.log"


def _ensure_appdata_dir():
    try:
        _APPDATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


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
            f"SteamGuard (Qt) session started {datetime.now():%Y-%m-%d %H:%M:%S}\n"
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
                f"{'='*60}\nSession ended {datetime.now():%Y-%m-%d %H:%M:%S}\n{'='*60}\n")
            _log_file_handle.close()
        except Exception:
            pass
        _log_file_handle = None


# ── Debug logging with secret redaction (unchanged logic) ─────────────────────

_debug_log_handle = None
_REDACT_PATTERNS = [
    (re.compile(r'(?i)(key|token|secret|password|authorization)["\s:=]+([A-Za-z0-9_\-\.]{8,})', re.I),
     r'\1=***REDACTED***'),
    (re.compile(r'\b[A-F0-9]{8}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{12}\b', re.I),
     '***KEY***'),
]


def _open_debug_log():
    global _debug_log_handle
    try:
        _ensure_appdata_dir()
        _debug_log_handle = open(_DEBUG_LOG_FILE, "a", encoding="utf-8", buffering=1)
        _debug_log_handle.write(
            f"\n{'='*60}\nSteamGuard (Qt) DEBUG session {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"Python {sys.version}\n{'='*60}\n")
    except Exception:
        _debug_log_handle = None


def _redact(msg: str) -> str:
    for pat, rep in _REDACT_PATTERNS:
        msg = pat.sub(rep, msg)
    return msg


def debug_log(msg: str, level: str = "INFO"):
    msg = _redact(msg)
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    line = f"[{ts}] [{level}] {msg}\n"
    if _debug_log_handle:
        try:
            _debug_log_handle.write(line)
        except Exception:
            pass


# ── Windows toast + sound feedback (unchanged) ────────────────────────────────

def _show_toast(title: str, msg: str):
    def _do():
        try:
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
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=6)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def _play_protect_sound():
    def _do():
        try:
            if winsound:
                winsound.Beep(880, 80)
                winsound.Beep(1320, 120)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


def _play_unprotect_sound():
    def _do():
        try:
            if winsound:
                winsound.Beep(660, 80)
                winsound.Beep(440, 120)
        except Exception:
            pass
    threading.Thread(target=_do, daemon=True).start()


# ═════════════════════════════════════════════════════════════════════════════
# BUSINESS LOGIC  (preserved verbatim from steamguard.py)
# ═════════════════════════════════════════════════════════════════════════════

# ── Valve AS32590 CM IP Ranges ────────────────────────────────────────────────
VALVE_CIDRS = [
    "162.254.192.0/21", "155.133.224.0/19", "103.10.124.0/23", "103.28.54.0/24",
    "153.254.86.0/24", "205.196.6.0/24", "208.64.200.0/21", "208.78.164.0/22",
    "205.185.194.0/23", "146.66.152.0/24", "146.66.155.0/24", "45.121.184.0/23",
    "190.217.33.0/24", "185.25.180.0/22", "192.69.96.0/22",
]
VALVE_CIDRS_V6 = ["2620:f9:8000::/48", "2620:f9::/44"]

_VALVE_NETS = [ip_network(c, strict=False) for c in VALVE_CIDRS]
_VALVE_NETS_V6 = [ip_network(c, strict=False) for c in VALVE_CIDRS_V6]

VALVE_PREFIXES = [
    "162.254.19", "155.133.2", "103.10.124", "103.10.125", "103.28.54",
    "153.254.86", "205.196.6", "208.64.20", "205.185.19", "146.66.1",
    "45.121.18", "190.217.33", "185.25.182", "185.25.183",
]

RULE_NAME = "SteamGuard_LockBypass"
STEAM_EXES = [
    r"C:\Program Files (x86)\Steam\steam.exe",
    r"C:\Program Files\Steam\steam.exe",
]


def refresh_cm_cidrs() -> tuple:
    """Query Valve's live CM server list and extend _VALVE_NETS with new IPs."""
    global _VALVE_NETS
    _ENDPOINTS = [
        ("https://api.steampowered.com/ISteamDirectory/GetCMListForConnect/v1/?cellid=0", "serverlist"),
        ("https://api.steampowered.com/ISteamDirectory/GetCMList/v1/?cellid=0", "serverlist"),
    ]
    live_ips = set()
    total = 0
    for url, key in _ENDPOINTS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SteamGuard/1.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            servers = data.get("response", {}).get(key, [])
            if not servers:
                continue
            total = len(servers)
            for entry in servers:
                if isinstance(entry, dict):
                    ep = entry.get("endpoint", "")
                    host = ep.rsplit(":", 1)[0] if ep else ""
                    kind = entry.get("type", "")
                else:
                    host = str(entry).rsplit(":", 1)[0]
                    kind = ""
                if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
                    live_ips.add(host)
                elif kind == "websocket" and host:
                    try:
                        for res in socket.getaddrinfo(host, None, socket.AF_INET):
                            live_ips.add(res[4][0])
                    except Exception:
                        pass
            break
        except Exception:
            continue
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


def _fetch_live_cm_servers() -> list:
    try:
        url = "https://api.steampowered.com/ISteamDirectory/GetCMList/v1/?cellid=0&maxcount=50"
        req = urllib.request.Request(url, headers={"User-Agent": "SteamGuard/1.4"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        servers = data.get("response", {}).get("serverlist", [])
        ips = list({s.split(":")[0] for s in servers if ":" in s})
        debug_log(f"Fetched {len(ips)} live CM IPs from Valve API")
        return ips if ips else []
    except Exception as e:
        debug_log(f"CM server fetch failed, using static CIDRs: {e}", level="WARN")
        return []


# ── DPAPI helpers ─────────────────────────────────────────────────────────────

def _dpapi_protect(data: bytes) -> bytes:
    try:
        import win32crypt
        return win32crypt.CryptProtectData(data, "SteamGuard", None, None, None, 0)
    except Exception:
        return data


def _dpapi_unprotect(blob: bytes) -> bytes:
    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
    except Exception:
        return blob


def hwid() -> str:
    """Stable hardware fingerprint for license binding."""
    import hashlib
    parts = []
    if winreg:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography") as k:
                parts.append(winreg.QueryValueEx(k, "MachineGuid")[0])
        except Exception:
            parts.append("no-guid")
    else:
        parts.append("no-guid")
    for q, fallback in (("wmic cpu get ProcessorId /format:value", "no-cpu"),
                        ("wmic baseboard get SerialNumber /format:value", "no-serial")):
        try:
            out = subprocess.check_output(q, shell=True, stderr=subprocess.DEVNULL,
                                          timeout=5).decode(errors="ignore")
            vals = [l.split("=", 1)[1].strip() for l in out.splitlines()
                    if "=" in l and l.strip()]
            parts.append(vals[0] if vals else fallback)
        except Exception:
            parts.append(fallback)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, f'"{os.path.abspath(__file__)}"', None, 1)
        if result > 32:
            sys.exit(0)
    except Exception:
        pass


def is_valve_ip(ip_str: str) -> bool:
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
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return True
        finally:
            sock.close()
    except OSError:
        return False


# ── Steam / Game Detection ────────────────────────────────────────────────────

def get_steam_exe():
    for p in STEAM_EXES:
        if os.path.exists(p):
            return p
    if not winreg:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\WOW6432Node\Valve\Steam") as key:
            path, _ = winreg.QueryValueEx(key, "InstallPath")
        exe = os.path.join(path, "steam.exe")
        return exe if os.path.exists(exe) else None
    except Exception:
        return None


def get_steam_install_dir():
    if not winreg:
        return None
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


def get_library_paths(steam_dir: str) -> list:
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


def build_game_catalog(steam_dir: str) -> dict:
    catalog = {}
    for lib in get_library_paths(steam_dir):
        for acf_path in glob.glob(os.path.join(lib, "appmanifest_*.acf")):
            data = parse_acf(acf_path)
            if "appid" not in data or "name" not in data:
                continue
            appid = int(data["appid"])
            game_dir = os.path.join(lib, "common", data.get("installdir", ""))
            exes = []
            if os.path.isdir(game_dir):
                try:
                    exes = [f.lower() for f in os.listdir(game_dir)
                            if f.lower().endswith(".exe")]
                except Exception:
                    pass
            catalog[appid] = {"name": data["name"], "game_dir": game_dir,
                              "exes": exes, "lib": lib}
    return catalog


def get_running_appid_registry():
    if not winreg:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam") as key:
            appid, _ = winreg.QueryValueEx(key, "RunningAppID")
        return int(appid) if appid and int(appid) != 0 else None
    except Exception:
        return None


def get_running_appid_apps_key():
    if not winreg:
        return None
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


def get_running_game(catalog: dict):
    appid = get_running_appid_registry()
    if appid and appid in catalog:
        return {"appid": appid, **catalog[appid], "method": "registry"}
    appid = get_running_appid_apps_key()
    if appid and appid in catalog:
        return {"appid": appid, **catalog[appid], "method": "apps_key"}
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


# ── Firewall Controller ───────────────────────────────────────────────────────

def run_ps(cmd: str) -> tuple:
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def run_netsh(args: str) -> int:
    r = subprocess.run(
        f"netsh advfirewall firewall {args}",
        shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode


def fw_rule_state():
    """None = no rule, True = enabled (blocking), False = disabled."""
    rc, out, _ = run_ps(
        f"(Get-NetFirewallRule -DisplayName '{RULE_NAME}' "
        f"-ErrorAction SilentlyContinue).Enabled")
    if rc != 0 or not out.strip():
        return None
    return out.strip().lower() == "true"


def _validate_cidrs_ps(cidrs: list) -> tuple:
    cidr_args = ",".join(f"'{c}'" for c in cidrs)
    script = (
        "Remove-NetFirewallRule -DisplayName '_SG_T' -ErrorAction SilentlyContinue | Out-Null; "
        f"$cidrs = @({cidr_args}); $out = @(); "
        "foreach ($c in $cidrs) { try { "
        "$null = New-NetFirewallRule -DisplayName '_SG_T' -Direction Outbound "
        "-RemoteAddress $c -Action Block -Enabled False -Profile Any -ErrorAction Stop; "
        "Remove-NetFirewallRule -DisplayName '_SG_T' -ErrorAction SilentlyContinue | Out-Null; "
        "$out += 'OK:' + $c } catch { $out += 'FAIL:' + $c } }; $out -join '|'"
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
        valid = list(cidrs)
    return valid, invalid


def fw_create(steam_exe: str) -> tuple:
    """Create the SteamGuard firewall rule — full outbound block to Valve CM IPs."""
    def _make_cmd(exe, cidrs):
        return (f"New-NetFirewallRule -DisplayName '{RULE_NAME}' -Direction Outbound "
                f"-Program '{exe}' -RemoteAddress {','.join(cidrs)} "
                f"-Action Block -Profile Any -Enabled False")

    def _make_v6_rule(exe):
        if not VALVE_CIDRS_V6:
            return
        run_ps(f"New-NetFirewallRule -DisplayName '{RULE_NAME}_v6' -Direction Outbound "
               f"-Program '{exe}' -RemoteAddress {','.join(VALVE_CIDRS_V6)} "
               f"-Action Block -Profile Any -Enabled False")

    rc, _, err = run_ps(_make_cmd(steam_exe, VALVE_CIDRS))
    if rc == 0:
        _make_v6_rule(steam_exe)
        return True, "OK"
    valid, invalid = _validate_cidrs_ps(VALVE_CIDRS)
    note = f"Skipped {len(invalid)} incompatible CIDR(s): {invalid}" if invalid else err
    if not valid:
        return False, f"No valid CIDRs — {err}"
    rc2, _, err2 = run_ps(_make_cmd(steam_exe, valid))
    ok = rc2 == 0
    if ok:
        _make_v6_rule(steam_exe)
    return ok, (note if ok else (err2 or err))


def _kill_valve_connections(sleep_after: bool = True) -> int:
    """Force-reset live Valve CM TCP connections via SetTcpEntry (iphlpapi)."""
    class MIB_TCPROW(ctypes.Structure):
        _fields_ = [
            ("dwState", ctypes.c_ulong), ("dwLocalAddr", ctypes.c_ulong),
            ("dwLocalPort", ctypes.c_ulong), ("dwRemoteAddr", ctypes.c_ulong),
            ("dwRemotePort", ctypes.c_ulong),
        ]
    MIB_TCP_STATE_DELETE_TCB = 12

    def ip_to_ulong(ip_str):
        try:
            return struct.unpack("<I", socket.inet_aton(ip_str))[0]
        except Exception:
            return 0

    def port_to_net(port):
        return socket.htons(port)

    killed = 0
    try:
        iphlp = ctypes.windll.iphlpapi
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
            row.dwState = MIB_TCP_STATE_DELETE_TCB
            row.dwLocalAddr = ip_to_ulong(c.laddr.ip)
            row.dwLocalPort = port_to_net(c.laddr.port)
            row.dwRemoteAddr = ip_to_ulong(rip)
            row.dwRemotePort = port_to_net(c.raddr.port)
            if iphlp.SetTcpEntry(ctypes.byref(row)) == 0:
                killed += 1
    except Exception:
        pass
    if killed and sleep_after:
        time.sleep(1.0)
    return killed


def fw_enable_fast() -> bool:
    run_netsh(f'set rule name="{RULE_NAME}_v6" new enable=yes')
    return run_netsh(f'set rule name="{RULE_NAME}" new enable=yes') == 0


def fw_disable_fast() -> bool:
    run_netsh(f'set rule name="{RULE_NAME}_v6" new enable=no')
    return run_netsh(f'set rule name="{RULE_NAME}" new enable=no') == 0


def fw_remove() -> bool:
    run_netsh(f'delete rule name="{RULE_NAME}_v6"')
    return run_netsh(f'delete rule name="{RULE_NAME}"') == 0


# ── Windows autostart helpers ─────────────────────────────────────────────────

_AUTOSTART_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_NAME = "SteamGuard"


def get_autostart() -> bool:
    if not winreg:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY) as k:
            winreg.QueryValueEx(k, _AUTOSTART_NAME)
            return True
    except Exception:
        return False


def set_autostart(enabled: bool) -> bool:
    if not winreg:
        return False
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


# ── Network Monitor (multi-layer, unchanged) ──────────────────────────────────

class NetworkMonitor:
    """Multi-layer network reconnect detector — kernel hint, psutil, WMI, poll."""

    def __init__(self, on_detected_callback):
        self._callback = on_detected_callback
        self._running = False
        self._steam_proc = None
        self._known_cm = set()
        self._active = True
        self._nlm_cb = None
        self._nlm_handle = None

    def start(self):
        self._running = True
        threading.Thread(target=self._connectivity_hint_loop, daemon=True).start()
        threading.Thread(target=self._psutil_loop, daemon=True).start()
        threading.Thread(target=self._wmi_loop, daemon=True).start()
        threading.Thread(target=self._internet_loop, daemon=True).start()

    def stop(self):
        self._running = False

    def set_active(self, active: bool):
        self._active = active
        if active:
            self._known_cm.clear()

    def _connectivity_hint_loop(self):
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
                if prev < 4 <= lvl and self._active:
                    self._callback("connectivity_hint", f"internet level {prev}->{lvl}")

            self._nlm_cb = _CB(_on_change)
            h = HANDLE()
            rc = _ip.NotifyNetworkConnectivityHintChange(self._nlm_cb, None, True, byref(h))
            if rc != 0:
                return
            self._nlm_handle = h
            while self._running:
                time.sleep(1)
            try:
                _ip.CancelMibChangeNotify2(h)
            except Exception:
                pass
        except Exception:
            pass

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
            import psutil  # noqa: F401
        except ImportError:
            return
        interval_normal, interval_alert = 0.5, 0.10
        _alert_until = 0
        while self._running:
            now = time.time()
            interval = interval_alert if now < _alert_until else interval_normal
            if self._active and self._steam_proc is None:
                self._steam_proc = self._find_steam_proc()
            elif not self._active:
                self._steam_proc = None
            if self._steam_proc and self._active:
                try:
                    if not self._steam_proc.is_running():
                        self._steam_proc = None
                        time.sleep(interval)
                        continue
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
                                _alert_until = time.time() + 10
                except Exception:
                    self._steam_proc = None
            time.sleep(interval)

    def _wmi_loop(self):
        try:
            import wmi as _wmi
            c = _wmi.WMI()
            watcher = c.Win32_NetworkAdapterConfiguration.watch_for("modification", delay_secs=1)
            while self._running:
                try:
                    event = watcher(timeout_ms=2000)
                    if event and event.IPEnabled and event.IPAddress:
                        if self._active:
                            self._callback("wmi_adapter", event.IPAddress[0])
                except Exception:
                    time.sleep(1)
        except ImportError:
            pass
        except Exception:
            pass

    @staticmethod
    def _connectivity_level() -> int:
        try:
            class _H(ctypes.Structure):
                _fields_ = [("level", ctypes.c_uint32), ("cost", ctypes.c_uint32)]
            h = _H()
            if ctypes.windll.iphlpapi.GetNetworkConnectivityHint(ctypes.byref(h)) == 0:
                return h.level
        except Exception:
            pass
        return 4 if has_internet() else 0

    def _internet_loop(self):
        was_level = self._connectivity_level()
        while self._running:
            time.sleep(2)
            now_level = self._connectivity_level()
            if was_level < 4 <= now_level and self._active:
                self._callback("internet_poll", "reconnected")
            was_level = now_level


# ═════════════════════════════════════════════════════════════════════════════
# QSS STYLESHEET
# ═════════════════════════════════════════════════════════════════════════════

MAIN_STYLE = """
QMainWindow, QWidget#central {
    background-color: #0B0D10;
}
QWidget#sidebar {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 rgba(10,12,15,0.90), stop:1 rgba(5,7,8,0.95));
    border-right: 1px solid rgba(255,255,255,0.06);
}
QPushButton#nav-btn {
    background: transparent;
    border: none;
    color: #94A3B8;
    font-size: 20px;
    padding: 12px;
    border-radius: 8px;
}
QPushButton#nav-btn:hover { color: #F8FAFC; background: rgba(255,255,255,0.04); }
QPushButton#nav-btn:checked {
    color: #3B82F6; background: rgba(59,130,246,0.14);
    border-left: 2px solid #3B82F6;
}
QFrame#card {
    background: rgba(15,17,20,0.72);
    border-radius: 12px;
    border: 1px solid rgba(255,255,255,0.06);
    border-top: 1px solid rgba(255,255,255,0.10);
}
QFrame#card:hover { border-color: rgba(59,130,246,0.4); }
QLabel#card-value { color: #F8FAFC; font-size: 24px; font-weight: 700; }
QLabel#card-label { color: #64748B; font-size: 11px; font-weight: 600; letter-spacing: 1px; }
QPushButton#btn-primary {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #4F8DF8, stop:1 #3475E8);
    color: white; border: none; border-radius: 8px;
    font-size: 13px; font-weight: 700; padding: 12px 24px;
}
QPushButton#btn-primary:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #6EA6FF, stop:1 #4F8DF8);
}
QPushButton#btn-primary:pressed { background: #2563EB; }
QPushButton#btn-primary:disabled { background: #1E2A3D; color: #64748B; }
QPushButton#btn-danger {
    background: transparent; color: #EF4444;
    border: 1px solid #EF4444; border-radius: 8px;
    font-size: 13px; font-weight: 700; padding: 12px 24px;
}
QPushButton#btn-danger:hover { background: rgba(239,68,68,0.10); border-color: #DC2626; }
QPushButton#btn-danger:disabled { color: #4A2A2A; border-color: #3A2222; }
QTextEdit#log {
    background: #0A0C10; color: #94A3B8;
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px; font-family: 'Consolas', 'JetBrains Mono', monospace; font-size: 12px;
    padding: 8px;
}
QScrollBar:vertical { background: transparent; width: 5px; border-radius: 2px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,0.15); border-radius: 2px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: rgba(59,130,246,0.50); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


# ═════════════════════════════════════════════════════════════════════════════
# THREAD-SAFE SIGNALS
# ═════════════════════════════════════════════════════════════════════════════

class LogSignal(QObject):
    """Worker threads emit (text, level) — delivered on the GUI thread."""
    message = Signal(str, str)


class UiBridge(QObject):
    """Generic thread→GUI bridge. Workers emit a callable to run on main thread."""
    run = Signal(object)


# ═════════════════════════════════════════════════════════════════════════════
# CUSTOM WIDGETS
# ═════════════════════════════════════════════════════════════════════════════

class TitleBar(QWidget):
    """Frameless custom 44px title bar (drag to move, minimize, close)."""

    def __init__(self, parent, icon_path=None):
        super().__init__(parent)
        self._win = parent
        self._drag_pos = None
        self.setFixedHeight(44)
        self.setStyleSheet(f"background-color: {C_BG};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(10)

        # RC logo with blue glow — matches the loader's login card treatment.
        logo = QLabel()
        here = Path(__file__).parent
        candidates = [here / "icon_256.png", here / "icon.png", here / "rc_logo_64.png"]
        if icon_path:
            candidates.insert(0, Path(icon_path))
        loaded = False
        for cand in candidates:
            if cand and os.path.exists(str(cand)):
                src = QPixmap(str(cand))
                if not src.isNull():
                    # 2x supersample then downsample for crisp edges on hi-dpi.
                    pm = src.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    pm.setDevicePixelRatio(2.0)
                    logo.setPixmap(pm)
                    logo.setFixedSize(40, 40)
                    loaded = True
                    break
        if not loaded:
            logo.setText("RC")
            logo.setStyleSheet(f"color: {C_ACCENT}; font-weight: 700; font-size: 13px;")
        glow = QGraphicsDropShadowEffect(logo)
        glow.setBlurRadius(22)
        glow.setOffset(0, 0)
        glow.setColor(C_ACCENT_GLOW)
        logo.setGraphicsEffect(glow)
        lay.addWidget(logo)

        title = QLabel("SteamGuard")
        title.setStyleSheet(
            f"color: {C_TEXT}; font-size: 13px; font-weight: 600; letter-spacing: 1px;")
        lay.addStretch(1)
        lay.addWidget(title)
        lay.addStretch(1)

        btn_min = QPushButton("\u2013")  # en-dash minus
        btn_close = QPushButton("\u2715")  # multiplication x
        for b in (btn_min, btn_close):
            b.setFixedSize(28, 24)
            b.setCursor(Qt.PointingHandCursor)
        btn_min.setStyleSheet(
            "QPushButton{background:transparent;color:#94A3B8;border:none;border-radius:4px;font-size:14px;}"
            "QPushButton:hover{background:rgba(255,255,255,0.08);color:#F8FAFC;}")
        btn_close.setStyleSheet(
            "QPushButton{background:transparent;color:#94A3B8;border:none;border-radius:4px;font-size:13px;}"
            "QPushButton:hover{background:rgba(239,68,68,0.15);color:#EF4444;}")
        btn_min.clicked.connect(self._win.showMinimized)
        btn_close.clicked.connect(self._win.close)
        lay.addWidget(btn_min)
        lay.addWidget(btn_close)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


class StatCard(QFrame):
    """A glass stat card with a small uppercase label and a large value."""

    def __init__(self, label_text, value_text="—"):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(8)
        self.label = QLabel(label_text.upper())
        self.label.setObjectName("card-label")
        self.value = QLabel(value_text)
        self.value.setObjectName("card-value")
        self.value.setWordWrap(True)
        lay.addWidget(self.label)
        lay.addWidget(self.value)
        lay.addStretch(1)

        # Blue hover glow (widget-level), invisible at rest.
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(24)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(59, 130, 246, 0))
        self.setGraphicsEffect(self._glow)

    def enterEvent(self, event):
        self._glow.setColor(QColor(59, 130, 246, 80))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glow.setColor(QColor(59, 130, 246, 0))
        super().leaveEvent(event)

    def set_value(self, text, color=None):
        self.value.setText(text)
        if color:
            self.value.setStyleSheet(f"color: {color}; font-size: 24px; font-weight: 700;")
        else:
            self.value.setStyleSheet("")


class ProtectionCard(StatCard):
    """Protection ON/OFF card with a semantic tinted border + persistent glow."""

    def _apply_glow(self, qcolor):
        self._glow.setColor(qcolor)

    # Keep the semantic (green/red) glow — don't clobber it on hover.
    def enterEvent(self, event):
        return

    def leaveEvent(self, event):
        return

    def set_protected(self, protected: bool):
        if protected:
            self.set_value("ON", C_SUCCESS)
            self.setStyleSheet(
                "QFrame#card{background:rgba(15,17,20,0.72);border-radius:12px;"
                "border:1px solid rgba(34,197,94,0.4);"
                "border-top:1px solid rgba(255,255,255,0.10);}")
            self._apply_glow(QColor(34, 197, 94, 70))  # subtle green inner glow
        else:
            self.set_value("OFF", C_ERROR)
            self.setStyleSheet(
                "QFrame#card{background:rgba(15,17,20,0.72);border-radius:12px;"
                "border:1px solid rgba(239,68,68,0.4);"
                "border-top:1px solid rgba(255,255,255,0.10);}")
            self._apply_glow(QColor(239, 68, 68, 70))  # subtle red inner glow


class TimeRingCard(StatCard):
    """Time-remaining card with a circular progress indicator drawn behind."""

    def __init__(self, label_text):
        super().__init__(label_text, "—")
        self._fraction = 0.0  # 0..1 of a reference window (e.g. relative to 8h)
        self._ring_color = QColor(C_ACCENT)

    def set_time(self, remaining_hours):
        if remaining_hours is None:
            self.set_value("—")
            self._fraction = 0.0
            self.update()
            return
        try:
            rh = float(remaining_hours)
        except (TypeError, ValueError):
            self.set_value("—")
            return
        h = int(rh)
        m = int(round((rh - h) * 60))
        if m == 60:
            h += 1
            m = 0
        if rh < 0.5:
            color = C_ERROR
        elif rh < 2:
            color = C_WARNING
        else:
            color = C_SUCCESS
        self._ring_color = QColor(color)
        self.set_value(f"{h}h {m}m", color)
        # fraction relative to an 8h reference for a pleasant arc
        self._fraction = max(0.0, min(1.0, rh / 8.0))
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        d = 34
        x = self.width() - d - 16
        y = 14
        rect = QRectF(x, y, d, d)
        # track
        pen = QPen(QColor(255, 255, 255, 15), 4)  # rgba(255,255,255,0.06)
        p.setPen(pen)
        p.drawArc(rect, 0, 360 * 16)
        # progress
        pen2 = QPen(self._ring_color, 4)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        span = int(-360 * 16 * self._fraction)
        p.drawArc(rect, 90 * 16, span)
        p.end()


class GlowButton(QPushButton):
    """QPushButton that fades a soft colored glow in on hover."""

    def __init__(self, text, glow_color=None):
        super().__init__(text)
        self._gc = glow_color or QColor(59, 130, 246, 150)
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setBlurRadius(26)
        self._glow.setOffset(0, 0)
        self._glow.setColor(QColor(self._gc.red(), self._gc.green(), self._gc.blue(), 0))
        self.setGraphicsEffect(self._glow)

    def enterEvent(self, event):
        if self.isEnabled():
            self._glow.setColor(self._gc)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._glow.setColor(QColor(self._gc.red(), self._gc.green(), self._gc.blue(), 0))
        super().leaveEvent(event)


class SessionGraphCard(QFrame):
    """Time-series graph of protection state over the last 24 hours."""

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        self.setMinimumHeight(180)
        # Glass surface (matches the loader treatment via shared constants).
        self.setStyleSheet(
            f"QFrame#card{{background:{GLASS_BG};border-radius:12px;"
            f"border:1px solid {GLASS_BORDER};"
            f"border-top:1px solid rgba(255,255,255,0.10);}}")
        self._range = "24h"          # or "7d"
        self._data: list[tuple[float, bool]] = []  # (hours_ago, is_protected)
        self._init_placeholder_data()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 12)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        self._title = QLabel("PROTECTED TIME — LAST 24H")
        self._title.setObjectName("card-label")
        head.addWidget(self._title)
        head.addStretch(1)
        self._chip_24h = QPushButton("24H")
        self._chip_7d = QPushButton("7D")
        for chip, rng in ((self._chip_24h, "24h"), (self._chip_7d, "7d")):
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setFixedHeight(22)
            chip.setStyleSheet(self._chip_css())
            chip.clicked.connect(lambda _=False, r=rng: self._set_range(r))
            head.addWidget(chip)
        self._chip_24h.setChecked(True)
        lay.addLayout(head)
        lay.addStretch(1)  # chart is painted in the region below the header

    @staticmethod
    def _chip_css() -> str:
        return (
            "QPushButton{background:transparent;color:#64748B;"
            "border:1px solid rgba(255,255,255,0.10);border-radius:11px;"
            "padding:2px 12px;font-size:10px;font-weight:700;letter-spacing:1px;}"
            "QPushButton:hover{color:#F8FAFC;border-color:rgba(59,130,246,0.5);}"
            "QPushButton:checked{color:#3B82F6;background:rgba(59,130,246,0.14);"
            "border-color:rgba(59,130,246,0.5);}")

    def _init_placeholder_data(self):
        """Placeholder: mostly-protected pattern with a few gaps.

        # TODO: wire to real event log data from HeartbeatWorker
        """
        import random
        random.seed(42)
        self._data = []
        for hour in range(24):
            state = random.random() > 0.1  # 90% chance protected
            self._data.append((24 - hour, state))

    def _set_range(self, rng: str):
        self._range = rng
        self._chip_24h.setChecked(rng == "24h")
        self._chip_7d.setChecked(rng == "7d")
        # 7D is a stub for now — only the label changes.
        self._title.setText("PROTECTED TIME — LAST 24H" if rng == "24h"
                            else "PROTECTED TIME — LAST 7D")
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)  # QSS glass background + border
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        L, R = 20, self.width() - 20
        T, B = 56, self.height() - 30
        if R <= L or B <= T:
            p.end()
            return
        plot_w = R - L
        plot_h = B - T
        n = len(self._data)
        y_hi = T + plot_h * 0.18
        y_lo = T + plot_h * 0.82

        def x_at(i):
            return L + plot_w * (i / (n - 1)) if n > 1 else L

        # Grid lines every 4 hours + hour labels (drawn behind the series).
        p.save()
        grid_pen = QPen(QColor(255, 255, 255, 10), 1)
        p.setPen(grid_pen)
        label_font = QFont()
        label_font.setPointSize(8)
        for i in range(0, n, 4):
            gx = x_at(i)
            p.setPen(grid_pen)
            p.drawLine(int(gx), T, int(gx), B)
            p.setPen(QPen(QColor("#64748B")))
            p.setFont(label_font)
            hours_ago = int(self._data[i][0])
            label = "now" if hours_ago <= 1 else f"-{hours_ago}h"
            p.drawText(QRectF(gx - 20, B + 4, 40, 16), Qt.AlignCenter, label)
        p.restore()

        # Build the stepped series.
        line = QPainterPath()
        prev_y = None
        for i, (_ha, st) in enumerate(self._data):
            xi = x_at(i)
            yi = y_hi if st else y_lo
            if i == 0:
                line.moveTo(xi, yi)
            else:
                line.lineTo(xi, prev_y)
                line.lineTo(xi, yi)
            prev_y = yi

        # Area fill under the line.
        area = QPainterPath(line)
        area.lineTo(R, B)
        area.lineTo(L, B)
        area.closeSubpath()
        p.fillPath(area, QBrush(QColor(59, 130, 246, 38)))  # rgba(59,130,246,0.15)

        # Series line on top.
        line_pen = QPen(QColor(C_ACCENT), 2)
        line_pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(line_pen)
        p.drawPath(line)
        p.end()


# ═════════════════════════════════════════════════════════════════════════════
# HEARTBEAT WORKER (QThread) — preserves the original HMAC heartbeat logic
# ═════════════════════════════════════════════════════════════════════════════

class HeartbeatWorker(QThread):
    sync_status = Signal(str, str)       # text, color
    remote_kill = Signal(str)            # reason
    time_expired = Signal()
    low_time = Signal(int)               # minutes
    log = Signal(str, str)               # text, level
    remaining = Signal(object)           # remaining_hours or None

    HMAC_SECRET = "7e3b9ccf02a09ad3520ebc7ed3f00a48d5eff34ef081900ee9064dba2a74529e"
    SERVER_URL = "https://steamguard-775181381055.us-central1.run.app"

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._running = True
        self._session_id = ""

    def stop(self):
        self._running = False

    def run(self):
        time.sleep(30)
        while self._running:
            try:
                self._do_heartbeat()
            except Exception as e:
                debug_log(f"heartbeat error: {e}", level="ERROR")
            for _ in range(300):
                if not self._running:
                    return
                time.sleep(1)

    def _do_heartbeat(self):
        if not _CONFIG_FILE.exists():
            return
        cfg = json.loads(_CONFIG_FILE.read_text("utf-8"))
        key = cfg.get("license_key", "")
        discord_id = cfg.get("discord_user_id", "")
        if not key or not discord_id:
            return
        import hashlib, hmac as _hmac, secrets
        try:
            from auth.hwid import get_hwid
            hwid_val = get_hwid()
        except Exception:
            try:
                hwid_val = hwid()
            except Exception:
                hwid_val = "unknown"
        nonce = secrets.token_hex(16)
        sig = _hmac.new(self.HMAC_SECRET.encode(),
                        f"{key}:{hwid_val}".encode(), hashlib.sha256).hexdigest()
        payload = json.dumps({
            "key": key, "hwid": hwid_val, "nonce": nonce,
            "discord_user_id": discord_id, "sig": sig,
            "client_version": CURRENT_VERSION, "session_id": self._session_id,
            "protected": self._app._protected,
            "heal_count": self._app._heal_count + self._app._rule_heal_cnt,
            "kill_count": self._app._kill_counter,
            "game_appid": self._app._running_appid or 0,
            "game_name": self._app._running_game["name"] if self._app._running_game else "",
        }).encode()
        req = urllib.request.Request(
            self.SERVER_URL + "/heartbeat", data=payload,
            headers={"Content-Type": "application/json",
                     "User-Agent": f"SteamGuard/{CURRENT_VERSION}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = json.loads(r.read())
        except Exception:
            self.sync_status.emit("Server sync: offline", C_WARNING)
            return
        echo = resp.get("nonce_echo")
        if echo is not None and echo != nonce:
            debug_log("Heartbeat nonce mismatch -- possible replay", level="WARN")
        if resp.get("session_id"):
            self._session_id = resp["session_id"]
        self.sync_status.emit("Server sync: OK", C_SUCCESS)
        if resp.get("kill"):
            self.remote_kill.emit(resp.get("kill_reason", "License revoked"))
            return
        remaining = resp.get("remaining_hours")
        if remaining is not None:
            try:
                remaining = float(remaining)
            except (TypeError, ValueError):
                remaining = None
        self.remaining.emit(remaining)
        if remaining is not None and remaining <= 0:
            self.time_expired.emit()
            return
        if remaining is not None and remaining < 2:
            self.low_time.emit(int(remaining * 60))
        policy = resp.get("client_policy", {})
        if policy.get("mandatory_update"):
            self.log.emit("A mandatory update is available. Please update SteamGuard.", "warn")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN WINDOW
# ═════════════════════════════════════════════════════════════════════════════

class SteamGuardWindow(QMainWindow):

    def __init__(self, session=None):
        super().__init__()
        self._session = session or {}
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setFixedSize(900, 600)
        self.setStyleSheet(MAIN_STYLE)

        _open_log_file()
        _open_debug_log()
        debug_log(f"SteamGuard (Qt) v{CURRENT_VERSION} starting")
        self._cfg = load_config()

        # ── State (mirrors the original SteamGuard tk.Tk app) ──────────────────
        self._admin = is_admin()
        self._steam_exe = get_steam_exe()
        self._steam_dir = get_steam_install_dir()
        self._catalog = {}
        self._running_game = None
        self._running_appid = None
        self._fw_state = None
        self._auto_heal = bool(self._cfg.get("auto_protect", True))
        self._protected = False
        self._protect_start = None
        self._protection_start_time = None
        self._total_protected_seconds = 0
        self._heal_count = 0
        self._rule_heal_cnt = 0
        self._last_heal_time = None
        self._session_start = datetime.now()
        self._app_running = True
        self._protection_busy = False
        self._kill_counter = 0
        self._remaining_hours = None
        self._rpc = None

        self._monitor = NetworkMonitor(self._on_cm_detected)

        # Thread→GUI bridges
        self._bridge = UiBridge()
        self._bridge.run.connect(lambda fn: fn())
        self._logsig = LogSignal()
        self._logsig.message.connect(self._append_log)

        self._build_ui()
        self._center_on_screen()
        self._fade_in()

        # Heartbeat worker
        self._hb = HeartbeatWorker(self)
        self._hb.sync_status.connect(self._on_sync_status)
        self._hb.remote_kill.connect(self._on_remote_kill)
        self._hb.time_expired.connect(self._on_time_expired)
        self._hb.low_time.connect(self._on_low_time)
        self._hb.log.connect(self._append_log)
        self._hb.remaining.connect(self._on_remaining)
        self._hb.start()

        # Timers (replace tk.after) — game detect + connection kill watchdog
        self._game_timer = QTimer(self)
        self._game_timer.timeout.connect(self._detect_game_tick)

        self._kill_timer = QTimer(self)
        self._kill_timer.setInterval(200)
        self._kill_timer.timeout.connect(self._connection_kill_tick)
        self._kill_timer.start()

        self._sched_timer = QTimer(self)
        self._sched_timer.setInterval(60000)
        self._sched_timer.timeout.connect(self._check_scheduled_protection)
        self._sched_timer.start()

        self._cm_refresh_timer = QTimer(self)
        self._cm_refresh_timer.setInterval(21600000)  # 6h
        self._cm_refresh_timer.timeout.connect(self._refresh_cm_rules)
        self._cm_refresh_timer.start()

        # Rule health self-heal loop (background thread)
        threading.Thread(target=self._rule_health_loop, daemon=True).start()

        # Initial load (background)
        self._populate_settings()
        threading.Thread(target=self._initial_load_worker, daemon=True).start()

    # ── thread-safe helper ─────────────────────────────────────────────────────
    def _ui(self, fn):
        """Post a callable to run on the GUI thread (replaces tk _UI_QUEUE)."""
        self._bridge.run.emit(fn)

    # ── UI construction ─────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        icon_path = str(Path(__file__).parent / "icon_256.png")
        self._title_bar = TitleBar(self, icon_path)
        outer.addWidget(self._title_bar)
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        body = QWidget()
        body_lay = QHBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(0)
        outer.addWidget(body, 1)

        # ── Sidebar ────────────────────────────────────────────────────────────
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(64)
        sb_lay = QVBoxLayout(sidebar)
        sb_lay.setContentsMargins(8, 14, 8, 14)
        sb_lay.setSpacing(8)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        nav_defs = [("\U0001F6E1", "Status"), ("\U0001F4DC", "Log"), ("\u2699", "Settings")]
        self._nav_buttons = []
        for i, (glyph, tip) in enumerate(nav_defs):
            b = QPushButton(glyph)
            b.setObjectName("nav-btn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setFixedSize(48, 48)
            b.clicked.connect(lambda _=False, idx=i: self._set_page(idx))
            self._nav_group.addButton(b, i)
            sb_lay.addWidget(b)
            self._nav_buttons.append(b)
        sb_lay.addStretch(1)
        body_lay.addWidget(sidebar)

        # ── Stacked content ─────────────────────────────────────────────────────
        self._stack = QStackedWidget()
        body_lay.addWidget(self._stack, 1)
        self._stack.addWidget(self._build_status_page())
        self._stack.addWidget(self._build_log_page())
        self._stack.addWidget(self._build_settings_page())

        self._nav_buttons[0].setChecked(True)
        self._set_page(0)

    def _set_page(self, idx):
        self._stack.setCurrentIndex(idx)
        for i, b in enumerate(self._nav_buttons):
            b.setChecked(i == idx)

    def _build_status_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        # ── Row 1: hero status card (state + game info + inline CTA) ────────────
        lay.addWidget(self._build_hero())

        # ── Row 2: stat cards grid (2x2) ────────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(14)
        self._card_protection = ProtectionCard("Protection", "OFF")
        self._card_game = StatCard("Game Detected", "None")
        self._card_time = TimeRingCard("Time Remaining")
        self._card_heals = StatCard("Heals Today", "0")
        for c in (self._card_protection, self._card_game, self._card_time, self._card_heals):
            c.setMinimumHeight(80)
            c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        grid.addWidget(self._card_protection, 0, 0)
        grid.addWidget(self._card_game, 0, 1)
        grid.addWidget(self._card_time, 1, 0)
        grid.addWidget(self._card_heals, 1, 1)
        lay.addLayout(grid)

        # ── Row 3: session graph (fills remaining space) ────────────────────────
        self._graph = SessionGraphCard()
        lay.addWidget(self._graph, 1)

        self._card_protection.set_protected(False)
        self._update_hero(False)
        return page

    def _build_hero(self):
        """Full-width hero status card: state indicator + game info + CTA."""
        frame = QFrame()
        frame.setObjectName("card")
        frame.setFixedHeight(104)
        self._hero_frame = frame
        h = QHBoxLayout(frame)
        h.setContentsMargins(24, 16, 20, 16)
        h.setSpacing(18)

        left = QVBoxLayout()
        left.setSpacing(6)
        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._hero_dot = QLabel()
        self._hero_dot.setFixedSize(12, 12)
        self._hero_status = QLabel("UNPROTECTED")
        self._hero_status.setStyleSheet(
            f"color:{C_ERROR};font-size:18pt;font-weight:700;"
            "letter-spacing:1px;background:transparent;")
        status_row.addWidget(self._hero_dot)
        status_row.addWidget(self._hero_status)
        status_row.addStretch(1)
        left.addLayout(status_row)
        self._hero_info = QLabel("Game: None detected  ·  Session: 0m")
        self._hero_info.setStyleSheet(
            f"color:{C_TEXT_DIM};font-size:11pt;background:transparent;")
        left.addWidget(self._hero_info)
        h.addLayout(left, 1)

        self._btn_start = GlowButton("START PROTECTION")
        self._btn_start.setObjectName("btn-primary")
        self._btn_start.setCursor(Qt.PointingHandCursor)
        self._btn_start.setMinimumHeight(42)
        self._btn_start.clicked.connect(self._start_protection)
        self._btn_stop = QPushButton("STOP")
        self._btn_stop.setObjectName("btn-danger")
        self._btn_stop.setCursor(Qt.PointingHandCursor)
        self._btn_stop.setMinimumHeight(42)
        self._btn_stop.setFixedWidth(120)
        self._btn_stop.clicked.connect(self._stop_protection)
        h.addWidget(self._btn_start)
        h.addWidget(self._btn_stop)
        return frame

    def _update_hero(self, protected: bool):
        if protected:
            self._hero_status.setText("PROTECTED")
            self._hero_status.setStyleSheet(
                f"color:{C_SUCCESS};font-size:18pt;font-weight:700;"
                "letter-spacing:1px;background:transparent;")
            self._hero_dot.setStyleSheet(f"background:{C_SUCCESS};border-radius:6px;")
            self._hero_frame.setStyleSheet(
                f"QFrame#card{{background:{GLASS_BG};border-radius:12px;"
                f"border:1px solid rgba(34,197,94,0.45);"
                "border-top:1px solid rgba(255,255,255,0.10);}")
        else:
            self._hero_status.setText("UNPROTECTED")
            self._hero_status.setStyleSheet(
                f"color:{C_ERROR};font-size:18pt;font-weight:700;"
                "letter-spacing:1px;background:transparent;")
            self._hero_dot.setStyleSheet(f"background:{C_ERROR};border-radius:6px;")
            self._hero_frame.setStyleSheet(
                f"QFrame#card{{background:{GLASS_BG};border-radius:12px;"
                f"border:1px solid {GLASS_BORDER};"
                "border-top:1px solid rgba(255,255,255,0.10);}")
        self._update_hero_info()

    def _update_hero_info(self):
        game = self._running_game["name"] if self._running_game else "None detected"
        if self._protection_start_time:
            secs = (datetime.now() - self._protection_start_time).total_seconds()
        else:
            secs = 0
        mins = int(secs // 60)
        dur = f"{mins // 60}h {mins % 60}m" if mins >= 60 else f"{mins}m"
        self._hero_info.setText(f"Game: {game}  ·  Session: {dur}")

    def _build_log_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Event Log")
        title.setStyleSheet(
            f"color: {C_TEXT}; font-size: 16px; font-weight: 700; letter-spacing: 1px;")
        header.addWidget(title)
        header.addStretch(1)
        clear_btn = QPushButton("Clear Log")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#94A3B8;border:1px solid rgba(255,255,255,0.1);"
            "border-radius:4px;padding:6px 14px;font-size:12px;font-weight:600;}"
            "QPushButton:hover{color:#F8FAFC;border-color:rgba(59,130,246,0.5);}")
        clear_btn.clicked.connect(lambda: self._log_view.clear())
        header.addWidget(clear_btn)
        lay.addLayout(header)

        self._log_view = QTextEdit()
        self._log_view.setObjectName("log")
        self._log_view.setReadOnly(True)
        lay.addWidget(self._log_view, 1)
        return page

    def _build_settings_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(14)

        title = QLabel("Settings")
        title.setStyleSheet(
            f"color: {C_TEXT}; font-size: 16px; font-weight: 700; letter-spacing: 1px;")
        lay.addWidget(title)

        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 8, 20, 8)
        cl.setSpacing(0)

        def row(label_text, divider=True):
            row_frame = QFrame()
            row_frame.setStyleSheet(
                "QFrame{background:transparent;" +
                ("border-bottom:1px solid rgba(255,255,255,0.04);" if divider else "") + "}")
            r = QHBoxLayout(row_frame)
            r.setContentsMargins(0, 12, 0, 12)
            lbl = QLabel(label_text.upper())
            lbl.setStyleSheet(
                f"color: {C_TEXT_MUTED}; font-size: 11px; font-weight: 600; "
                "letter-spacing: 1px; border: none;")
            lbl.setFixedWidth(150)
            val = QLabel("—")
            val.setStyleSheet(f"color: {C_TEXT}; font-size: 13px; border: none;")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            r.addWidget(lbl)
            r.addWidget(val, 1)
            cl.addWidget(row_frame)
            return val

        self._set_discord_val = row("Discord User ID")
        self._set_key_val = row("License Key")
        self._set_time_val = row("Time Remaining", divider=False)
        lay.addWidget(card)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        dash_btn = QPushButton("Open Dashboard")
        dash_btn.setObjectName("btn-primary")
        dash_btn.setCursor(Qt.PointingHandCursor)
        dash_btn.setMinimumHeight(40)
        dash_btn.clicked.connect(lambda: webbrowser.open("https://rivvak.app/dashboard/index.html"))
        logout_btn = QPushButton("Logout")
        logout_btn.setObjectName("btn-danger")
        logout_btn.setCursor(Qt.PointingHandCursor)
        logout_btn.setMinimumHeight(40)
        logout_btn.clicked.connect(self._logout)
        btn_row.addWidget(dash_btn)
        btn_row.addWidget(logout_btn)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        lay.addStretch(1)
        return page

    def _populate_settings(self):
        cfg = load_config()
        discord_id = cfg.get("discord_user_id", "") or self._session.get("discord_user_id", "")
        key = cfg.get("license_key", "") or self._session.get("key", "")
        self._set_discord_val.setText(discord_id or "—")
        self._set_key_val.setText(self._mask_key(key) if key else "—")
        self._set_time_val.setText("—")

    @staticmethod
    def _mask_key(key: str) -> str:
        """Mask a license key as SG-XXXX-****-****."""
        clean = key.replace("-", "")
        first = clean[:4].upper() if clean else "XXXX"
        return f"SG-{first}-****-****"

    def _logout(self):
        if QMessageBox.question(self, "SteamGuard — Logout",
                                "Log out and clear your saved license?") != QMessageBox.Yes:
            return
        try:
            cfg = load_config()
            for k in ("license_key", "discord_user_id", "session_token_dpapi"):
                cfg.pop(k, None)
            save_config(cfg)
        except Exception:
            pass
        try:
            from auth.cache import clear_session
            clear_session()
        except Exception:
            pass
        self._append_log("Logged out. Restart SteamGuard to sign in again.", "warn")
        self._populate_settings()

    # ── window animation helpers ────────────────────────────────────────────────
    def _center_on_screen(self):
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2,
                  scr.center().y() - self.height() // 2)

    def _fade_in(self):
        self._win_opacity = QGraphicsOpacityEffect(self)
        # Apply fade on the central widget so child effects (banner) still work.
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(280)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.start()

    # ═════════════════════════════════════════════════════════════════════════════
    # LOG  (color-coded, auto-scroll, timestamp prefix)
    # ═════════════════════════════════════════════════════════════════════════════

    def _log(self, text: str, level: str = "info"):
        """Public log entry — safe from any thread (emits via signal)."""
        self._logsig.message.emit(text, level)

    def _color_for_line(self, text: str, level: str) -> str:
        up = text.upper()
        if "ERROR" in up or "FAIL" in up or level == "error":
            return C_ERROR
        if "WARN" in up or level == "warn":
            return C_WARNING
        if ("OK" in up or "success" in text.lower() or "activated" in text.lower()
                or level in ("success", "heal")):
            return C_SUCCESS
        if "detected" in text.lower() or "game" in text.lower():
            return C_ACCENT
        if level == "kill":
            return C_WARNING
        return C_TEXT_DIM

    def _append_log(self, text: str, level: str = "info"):
        """Runs on the GUI thread. Appends a color-coded, timestamped line."""
        log_to_file(text)
        ts = datetime.now().strftime("%H:%M:%S")
        color = self._color_for_line(text, level)
        cursor = self._log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(f"[{ts}]  {text}\n", fmt)
        # Trim to keep memory bounded
        doc = self._log_view.document()
        if doc.blockCount() > 600:
            c2 = QTextCursor(doc)
            c2.movePosition(QTextCursor.Start)
            c2.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, 100)
            c2.removeSelectedText()
        sb = self._log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ═════════════════════════════════════════════════════════════════════════════
    # INITIAL LOAD  (firewall setup + library scan + monitors)
    # ═════════════════════════════════════════════════════════════════════════════

    def _initial_load_worker(self):
        self._log("Fetching live CM server list from Valve API…")
        new_ips, total = refresh_cm_cidrs()
        base = len(VALVE_CIDRS)
        if total:
            extra = f", +{new_ips} new IPs discovered" if new_ips else ""
            self._log(f"CM list: {total} live servers fetched ({base} static CIDRs{extra}).")
        else:
            self._log(f"CM API unreachable — using {base} built-in CIDRs.")

        if self._steam_dir:
            self._log("Scanning Steam library…")
            self._catalog = build_game_catalog(self._steam_dir)
            self._log(f"Found {len(self._catalog)} installed games.")

        state = fw_rule_state()
        if state is True:
            self._log("Firewall rule was already ACTIVE (previous session). Resuming protection — "
                      "Steam CM traffic is blocked.", "success")
            self._protected = True
            self._protect_start = datetime.now()
            self._protection_start_time = datetime.now()
            self._ui(lambda: self._sync_fw_state(True))
            self._ui(self._update_protect_ui)
        elif state is not None:
            fw_remove()
            self._log("Existing rule removed — recreating fresh (full block).")
            self._ui(lambda: self._sync_fw_state(None))
            self._create_inactive_rule()
        else:
            self._ui(lambda: self._sync_fw_state(None))
            self._create_inactive_rule()

        self._monitor.start()
        self._log("Network monitor active (4 layers).")
        # Kick off game detection on the GUI thread
        self._ui(lambda: self._game_timer.start(3000))

    def _create_inactive_rule(self):
        if not self._steam_exe:
            self._log("Steam.exe not found — can't create firewall rule.", "warn")
            return
        ok, msg = fw_create(self._steam_exe)
        if ok:
            self._log("Firewall rule created (inactive). Ready — launch your game, "
                      "click START PROTECTION, then friend joins.")
            self._ui(lambda: self._sync_fw_state(False))
        else:
            self._log(f"Could not create rule: {msg}", "error")

    # ═════════════════════════════════════════════════════════════════════════════
    # GAME DETECTION  (QTimer-driven; spawns a short worker per tick)
    # ═════════════════════════════════════════════════════════════════════════════

    def _detect_game_tick(self):
        threading.Thread(target=self._detect_game_worker, daemon=True).start()
        # adapt interval like the original (5s when in-game, 3s when idle)
        self._game_timer.setInterval(5000 if self._running_appid else 3000)

    def _detect_game_worker(self):
        game = get_running_game(self._catalog)
        new_id = game["appid"] if game else None
        prev_id = self._running_appid
        self._running_game = game
        self._running_appid = new_id
        if new_id != prev_id:
            if game:
                g = game
                self._ui(lambda: self._update_game_card(g))
                self._log(f"Detected: {g['name']} (AppID {g['appid']})")
                if self._auto_heal and not self._protected:
                    import random
                    delay = random.randint(5000, 8000)
                    self._ui(lambda d=delay: QTimer.singleShot(d, self._auto_protect_if_still_running))
            else:
                self._ui(lambda: self._update_game_card(None))
                if self._protected:
                    self._ui(self._stop_protection)

    def _update_game_card(self, game):
        if game:
            self._card_game.set_value(game["name"], C_ACCENT)
        else:
            self._card_game.set_value("None")
        self._update_hero_info()

    def _auto_protect_if_still_running(self):
        if self._auto_heal and self._running_appid and not self._protected:
            self._log("Auto-protect: game logged in, enabling protection…")
            self._start_protection()

    # ═════════════════════════════════════════════════════════════════════════════
    # AUTO-HEAL  (network reconnect → re-apply block + kill CM connections)
    # ═════════════════════════════════════════════════════════════════════════════

    def _on_cm_detected(self, source: str, detail: str):
        """Called from a background monitor thread."""
        if not self._protected:
            return
        threading.Thread(target=self._auto_heal_worker, args=(source, detail),
                         daemon=True).start()

    def _auto_heal_worker(self, source: str, detail: str):
        if not self._protected:
            return
        ok = fw_enable_fast()
        if not ok:
            ok = fw_rule_state() is True
            if not ok:
                self._log(f"AUTO-HEAL FAILED [{source}]: {detail}", "error")
                return
        killed = _kill_valve_connections()
        self._ui(lambda: self._auto_heal_done(source, detail, killed))

    def _auto_heal_done(self, source: str, detail: str, killed: int):
        if not self._protected:
            return
        self._heal_count += 1
        self._last_heal_time = datetime.now()
        extra = f", severed {killed} connection(s)" if killed else ""
        self._append_log(f"AUTO-HEAL #{self._heal_count} [{source}]  "
                         f"Block re-applied{extra}. ({detail})", "heal")
        self._sync_fw_state(True)
        self._kill_counter += killed
        self._update_heal_card()

    def _update_heal_card(self):
        total = self._heal_count + self._rule_heal_cnt
        self._card_heals.set_value(str(total), C_SUCCESS if total else None)

    # ═════════════════════════════════════════════════════════════════════════════
    # PROTECTION ON/OFF
    # ═════════════════════════════════════════════════════════════════════════════

    def _start_protection(self):
        if not self._admin:
            self._append_log("Need admin to manage firewall rules.", "error")
            return
        if self._protection_busy or self._protected:
            return
        if not self._running_game:
            self._append_log("⚠  No game detected yet. CORRECT ORDER: launch the game first, "
                             "then click START PROTECTION, then your friend launches.", "warn")
        self._protection_busy = True
        self._btn_start.setEnabled(False)
        self._btn_start.setText("ACTIVATING…")
        threading.Thread(target=self._start_protection_worker, daemon=True).start()

    def _start_protection_worker(self):
        state = fw_rule_state()
        if state is None:
            if self._steam_exe:
                ok, msg = fw_create(self._steam_exe)
                if not ok:
                    self._log(f"Rule creation failed: {msg}", "error")
                    self._ui(self._finish_protection_busy)
                    return
            else:
                self._log("Steam.exe not found — can't create rule.", "error")
                self._ui(self._finish_protection_busy)
                return
        ok = fw_enable_fast()
        if ok:
            ok = fw_rule_state() is True
        if ok:
            killed = _kill_valve_connections()
            if killed:
                self._kill_counter += killed
                self._log(f"Severed {killed} live Valve CM connection(s) — Steam now offline.", "warn")
        self._ui(lambda: self._on_protection_started(ok))

    def _finish_protection_busy(self):
        self._protection_busy = False
        self._update_protect_ui()

    def _on_protection_started(self, ok: bool):
        self._protection_busy = False
        if ok:
            self._protected = True
            self._protect_start = datetime.now()
            self._protection_start_time = datetime.now()
            self._monitor.set_active(True)
            self._sync_fw_state(True)
            self._update_protect_ui()
            game_name = self._running_game["name"] if self._running_game else "manual"
            self._append_log(f"Protection ON  —  {game_name}. Auto-heal watching network.", "success")
            _play_protect_sound()
            _show_toast("SteamGuard — Protected",
                        f"Library lock blocked for {game_name}. Tell your friend to launch now.")
        else:
            self._update_protect_ui()
            self._append_log("Failed to enable firewall rule — check admin rights.", "error")

    def _stop_protection(self):
        if self._protection_busy:
            return
        self._protection_busy = True
        self._btn_start.setEnabled(False)
        self._btn_start.setText("STOPPING…")
        threading.Thread(target=self._stop_protection_worker, daemon=True).start()

    def _stop_protection_worker(self):
        ok = fw_disable_fast()
        self._ui(lambda: self._on_protection_stopped(ok))

    def _on_protection_stopped(self, ok: bool):
        self._protection_busy = False
        self._protected = False
        self._protect_start = None
        if self._protection_start_time:
            duration = (datetime.now() - self._protection_start_time).total_seconds()
            self._total_protected_seconds += duration
            self._protection_start_time = None
        self._monitor.set_active(False)
        self._sync_fw_state(False)
        self._update_protect_ui()
        self._append_log("Protection OFF  —  Steam can connect normally.")
        _play_unprotect_sound()
        _show_toast("SteamGuard — Unprotected", "Steam can now reach Valve servers.")

    def _update_protect_ui(self):
        self._btn_start.setEnabled(not self._protected and not self._protection_busy)
        self._btn_start.setText("PROTECTED" if self._protected else "START PROTECTION")
        self._btn_stop.setEnabled(self._protected and not self._protection_busy)
        self._card_protection.set_protected(self._protected)
        self._update_hero(self._protected)

    def _sync_fw_state(self, state):
        self._fw_state = state

    # ═════════════════════════════════════════════════════════════════════════════
    # RULE SELF-HEAL  +  CONNECTION KILL WATCHDOG
    # ═════════════════════════════════════════════════════════════════════════════

    def _rule_health_loop(self):
        time.sleep(15)
        while self._app_running:
            time.sleep(30)
            if not self._steam_exe:
                continue
            try:
                state = fw_rule_state()
                if state is None:
                    ok, _msg = fw_create(self._steam_exe)
                    if ok:
                        if self._protected:
                            fw_enable_fast()
                            self._ui(lambda: self._rule_healed("recreated & re-enabled"))
                        else:
                            self._ui(lambda: self._sync_fw_state(False))
                            self._log("Rule self-heal: rule was deleted — recreated (inactive).")
                elif state is False and self._protected:
                    fw_enable_fast()
                    self._ui(lambda: self._rule_healed("re-enabled"))
            except Exception:
                pass

    def _rule_healed(self, action: str):
        self._rule_heal_cnt += 1
        self._last_heal_time = datetime.now()
        self._sync_fw_state(True)
        self._append_log(f"RULE SELF-HEAL #{self._rule_heal_cnt}: rule was {action} automatically.", "heal")
        self._update_heal_card()

    def _connection_kill_tick(self):
        """200ms QTimer watchdog — runs the kill in a worker so the GUI never blocks."""
        if not self._protected:
            return
        threading.Thread(target=self._connection_kill_worker, daemon=True).start()

    def _connection_kill_worker(self):
        try:
            killed = _kill_valve_connections(sleep_after=False)
            if killed:
                self._kill_counter += killed
                self._log(f"Connection watchdog: severed {killed} Valve connection(s).", "kill")
        except Exception:
            pass

    # ═════════════════════════════════════════════════════════════════════════════
    # CM REFRESH  +  SCHEDULED PROTECTION
    # ═════════════════════════════════════════════════════════════════════════════

    def _refresh_cm_rules(self):
        threading.Thread(target=self._refresh_cm_worker, daemon=True).start()

    def _refresh_cm_worker(self):
        try:
            live_ips = _fetch_live_cm_servers()
            new_count = 0
            for ip_str in live_ips:
                try:
                    addr = ip_address(ip_str)
                    if not any(addr in net for net in _VALVE_NETS):
                        _VALVE_NETS.append(ip_network(f"{ip_str}/32"))
                        new_count += 1
                except ValueError:
                    pass
            if live_ips:
                self._log(f"CM refresh: {len(live_ips)} live CM IPs checked"
                          f"{f', +{new_count} new added' if new_count else ' (no new IPs)'}.")
            else:
                self._log("CM refresh: Valve API unreachable - keeping current CIDRs.")
            if self._protected and self._steam_exe:
                fw_remove()
                ok, msg = fw_create(self._steam_exe)
                if ok:
                    fw_enable_fast()
                    if fw_rule_state() is True:
                        self._log("CM refresh: firewall rule rebuilt with latest CM ranges.", "success")
                    else:
                        self._log("CM refresh: rule rebuilt but re-enable unconfirmed.", "warn")
                else:
                    self._log(f"CM refresh: rule rebuild failed: {msg}", "error")
        except Exception as e:
            debug_log(f"_refresh_cm_worker error: {e}", level="ERROR")

    def _check_scheduled_protection(self):
        schedule = self._cfg.get("schedule", {})
        if not schedule.get("enabled", False):
            return
        now = datetime.now()
        start_h, start_m = schedule.get("start_hour", 18), schedule.get("start_min", 0)
        end_h, end_m = schedule.get("end_hour", 23), schedule.get("end_min", 0)
        start_mins = start_h * 60 + start_m
        end_mins = end_h * 60 + end_m
        now_mins = now.hour * 60 + now.minute
        should_protect = start_mins <= now_mins < end_mins
        if should_protect and not self._protected:
            self._append_log(f"Scheduled protection: activating "
                             f"(scheduled {start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d})")
            self._start_protection()
        elif not should_protect and self._protected and schedule.get("auto_stop", False):
            self._append_log("Scheduled protection: window ended, disabling")
            self._stop_protection()

    # ═════════════════════════════════════════════════════════════════════════════
    # HEARTBEAT CALLBACKS  (GUI thread)
    # ═════════════════════════════════════════════════════════════════════════════

    def _on_sync_status(self, text, color):
        debug_log(f"sync: {text}")

    def _on_remote_kill(self, reason):
        if self._protected:
            self._stop_protection()
        self._append_log(f"Remote kill received: {reason}", "error")
        QMessageBox.critical(self, "SteamGuard — Access Revoked",
                             f"Your license has been deactivated:\n\n{reason}\n\nContact support in Discord.")

    def _on_time_expired(self):
        if self._protected:
            self._stop_protection()
        self._append_log("⏰ Your protection time has run out. Earn more time via rewards at rivvak.app", "warn")
        if QMessageBox.question(
                self, "SteamGuard — Time Expired",
                "Your SteamGuard protection time has run out.\n\n"
                "Earn free time via the rewards system:\n"
                "  • Daily check-in (+30 min)\n"
                "  • Invite a friend (+3h)\n"
                "  • YouTube sub (+2h)\n\n"
                "Open rivvak.app to claim rewards?") == QMessageBox.Yes:
            webbrowser.open("https://rivvak.app/dashboard/index.html")

    def _on_low_time(self, mins):
        self._append_log(f"⏰ Low time warning: {mins}m of protection time remaining. "
                         f"Earn more via rewards at rivvak.app", "warn")

    def _on_remaining(self, remaining):
        self._remaining_hours = remaining
        self._card_time.set_time(remaining)
        if remaining is None:
            self._set_time_val.setText("—")
        else:
            try:
                rh = float(remaining)
                h = int(rh)
                m = int(round((rh - h) * 60))
                self._set_time_val.setText(f"{h}h {m}m")
            except (TypeError, ValueError):
                self._set_time_val.setText("—")

    # ═════════════════════════════════════════════════════════════════════════════
    # CLOSE
    # ═════════════════════════════════════════════════════════════════════════════

    def closeEvent(self, event):
        self._app_running = False
        try:
            self._hb.stop()
        except Exception:
            pass
        try:
            self._monitor.stop()
        except Exception:
            pass
        if self._protected and self._fw_state:
            fw_disable_fast()
        if self._rpc is not None:
            try:
                self._rpc.close()
            except Exception:
                pass
        _close_log_file()
        event.accept()


# ═════════════════════════════════════════════════════════════════════════════
# ENTRY POINT  (preserves the original preflight / elevation flow)
# ═════════════════════════════════════════════════════════════════════════════

def main():
    _ensure_appdata_dir()
    _open_debug_log()
    debug_log("Process started (Qt)")

    # UAC elevation
    if not is_admin():
        elevate()

    app = QApplication(sys.argv)
    app.setApplicationName("SteamGuard")

    # TOS + License preflight (Qt version)
    session = None
    try:
        from auth.screens_qt import run_preflight
        session = run_preflight()
    except SystemExit:
        raise
    except ImportError:
        pass  # auth/ not present (dev/testing mode)
    except Exception as e:
        QMessageBox.critical(None, "SteamGuard", f"License check failed:\n{e}")
        sys.exit(1)

    win = SteamGuardWindow(session=session)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
