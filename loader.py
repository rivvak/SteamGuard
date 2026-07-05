"""
SteamGuard Loader — Rivvak Community edition
============================================
A PyQt5 front-end for the SteamGuard client, restyled to a **Dear ImGui dark
gaming-tool aesthetic**: near-black backgrounds, zero corner radius, 1px
desaturated borders everywhere, an electric-blue accent for hover/active
states, and a compact monospace font.

Every icon and decorative shape is still drawn with QPainter — there is ZERO
base64 image loading, ZERO QPixmap.loadFromData, and ZERO PIL usage for
display. Backgrounds for the product cards are painted QPainter linear
gradients (no bitmap art).

Screens:
  * Login  — dark ImGui card over a dimmed background (initial screen).
  * Dashboard — Popular products / Rewards / Referrals / Settings tabs.
  * Loading — concentric arc spinner during app init.

All networking runs on QThread workers — the UI thread is never blocked.

Backend: rivvak.app (override with the SG_SERVER_URL env var).
"""

import sys
import os
import json
import math
import hashlib
import webbrowser
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

from PyQt5.QtCore import (
    Qt, QSize, QTimer, QThread, pyqtSignal, QPoint, QRectF, QRect,
    QPropertyAnimation, QEasingCurve, pyqtProperty, QObject,
)
from PyQt5.QtGui import (
    QColor, QPainter, QPen, QBrush, QPolygonF, QFont, QFontMetrics, QIcon,
    QLinearGradient, QRadialGradient, QPainterPath, QPixmap, QImage,
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QCheckBox, QFrame, QGraphicsOpacityEffect,
    QStackedWidget, QScrollArea, QComboBox, QSizePolicy,
    QGraphicsDropShadowEffect,
)


def _apply_acrylic(hwnd):
    """Apply Windows Acrylic (frosted glass) backdrop to a window handle.
    Works on Windows 10 1903+ and Windows 11. Silent no-op on failure."""
    try:
        import ctypes
        import ctypes.wintypes as wt

        # Try Win11 Mica/Acrylic first (DwmSetWindowAttribute DWMWA_SYSTEMBACKDROP_TYPE=3)
        DWMWA_SYSTEMBACKDROP_TYPE = 38
        DWMSBT_TRANSIENTWINDOW = 3  # Acrylic
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                ctypes.byref(ctypes.c_int(DWMSBT_TRANSIENTWINDOW)),
                ctypes.sizeof(ctypes.c_int)
            )
            return
        except Exception:
            pass

        # Fallback: Win10 SetWindowCompositionAttribute (acrylic brush)
        class _ACCENT(ctypes.Structure):
            _fields_ = [
                ("AccentState",   ctypes.c_int),
                ("AccentFlags",   ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId",   ctypes.c_int),
            ]
        class _WCA_DATA(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int),
                        ("pData",     ctypes.c_void_p),
                        ("cbData",    ctypes.c_ulong)]
        accent = _ACCENT(AccentState=4,  # ACCENT_ENABLE_ACRYLICBLURBEHIND
                         AccentFlags=2,
                         GradientColor=0x990F0F0F,  # #0F0F0F @ 60% = dark tinted acrylic
                         AnimationId=0)
        data = _WCA_DATA(Attribute=19,  # WCA_ACCENT_POLICY
                         pData=ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                         cbData=ctypes.sizeof(accent))
        ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
    except Exception:
        pass  # Not Windows or API unavailable


# ══════════════════════════════════════════════════════════════════════════════
#  EXACT ImGui dark-theme colour palette (source-of-truth hex values)
# ══════════════════════════════════════════════════════════════════════════════

BG          = "#0B0D10"   # WindowBg — near-black graphite
BG_CHILD    = "#11151B"   # ChildBg — cards/panels
BG_POPUP    = "#141922"   # PopupBg — dropdowns/tooltips
BORDER      = "#2A3340"   # 1px borders everywhere
ACCENT      = "#3B82F6"   # Electric blue — hover/active/checkmarks
ACCENT_ACT  = "#2563EB"   # Pressed state — deeper blue
ACCENT_DIM  = "#1E3A5F"   # Button idle — accent @ 40% over bg
FRAME_BG    = "#111E2E"   # Input/frame background
HEADER      = "#172A45"   # Active nav item background
TEXT        = "#F8FAFC"   # Primary text
TEXT_DIM    = "#94A3B8"   # Disabled/secondary text
TITLE_BG    = "#080A0D"   # Sidebar/title bar

# Hover wash for nav items
NAV_HOVER   = "#111E31"

# ── Legacy-compatible aliases kept so preserved logic keeps working ────────────
ACCENT2      = ACCENT
RED          = "#F23F43"
RED_HOVER    = "#DC2626"
YELLOW       = ACCENT
MUTED        = TEXT_DIM
MUTED2       = TEXT_DIM
SURFACE      = BG_CHILD
CARD_SURFACE = BG_CHILD
ACCENT_HOVER = ACCENT
ACCENT_GOLD  = ACCENT       # login/CTA is now blue, not gold (ImGui aesthetic)
ACCENT_BLUE  = ACCENT
SPINNER_GREEN = "#35B18E"   # loading spinner outer arc
SPINNER_BLUE  = ACCENT      # loading spinner inner arc
ICON_GREY     = TEXT_DIM
TEXT_MUTED    = TEXT_DIM
PLACEHOLDER   = TEXT_DIM
INPUT_DASH    = ACCENT_DIM
TEXT_MUTED_HEX = "#D0D2D8"

APP_VERSION = "v2.0"

DISCORD_INVITE = "https://discord.gg/RTHM8YhpE"
BRAND_SITE     = "https://rivvak.app"

# ── Brand assets (loaded directly from disk — no base64, no PIL) ──────────────
_APP_DIR       = Path(__file__).resolve().parent
ASSETS_DIR     = _APP_DIR / "assets"
# Prefer the high-res 256px master; scale down for crisp edges on a small QLabel.
# rc_logo_64.png is kept as a last-ditch fallback for older bundles.
LOGO_PATH      = _APP_DIR / "icon_256.png"
LOGO_PATH_ALT  = _APP_DIR / "icon.png"
LOGO_PATH_LEG  = _APP_DIR / "rc_logo_64.png"
DISCORD_SVG    = ASSETS_DIR / "discord.svg"
YOUTUBE_SVG    = ASSETS_DIR / "youtube.svg"
WARNING_SVG    = ASSETS_DIR / "warning.svg"
DISCORD_PNG    = _APP_DIR / "discord_icon_24.png"  # legacy PNG fallback
YOUTUBE_PNG    = _APP_DIR / "youtube_icon_24.png"  # legacy PNG fallback


def _load_icon(*candidates):
    """Return the first QIcon that loads a non-null pixmap, else a null QIcon."""
    for path in candidates:
        try:
            if path and Path(path).exists():
                icon = QIcon(str(path))
                if not icon.isNull():
                    return icon
        except Exception:
            continue
    return QIcon()

# Default license/API server. The auth API is served under rivvak.app; override
# via the SG_SERVER_URL env var.
DEFAULT_SERVER_URL = os.environ.get("SG_SERVER_URL", "https://rivvak.app")

# ── Tool download (loader fetches SteamGuard.exe via rivvak.app/get-tool) ─────
STEAMGUARD_DOWNLOAD_URL = f"{DEFAULT_SERVER_URL}/get-tool?tool=steamguard"
TOOLS_DIR = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "SteamGuard" / "tools"
BUNDLED_TOOLS_DIR = Path(__file__).resolve().parent / "tools"
ROBLOX_COPY_TOOL_DIR = BUNDLED_TOOLS_DIR / "roblox_copy_tool"
ROBLOX_COPIER_EXE = ROBLOX_COPY_TOOL_DIR / "RobloxCopyTool.exe"
ROBLOX_COPY_TOOL_ENTRY = ROBLOX_COPY_TOOL_DIR / "roblox_copy_tool.py"
# Legacy fallback paths (older builds shipped these locations).
ROBLOX_COPIER_EXE_LEGACY = BUNDLED_TOOLS_DIR / "roblox_copier.exe"
ROBLOX_COPIER_PKG = Path(__file__).resolve().parent / "roblox_copier"


def ensure_tools_dir() -> None:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)


def get_local_tool_path(tool_name: str = "SteamGuard.exe") -> Path:
    """Returns the local cached path for a downloaded tool."""
    return TOOLS_DIR / tool_name


# Loader identity token — sent with every /get-tool request so the server
# knows the request came from the official loader, not a browser.
# The server validates this header before allowing the download.
LOADER_IDENTITY_TOKEN = "SteamGuard-Loader-Official-v2-Rivvak"

# ── Persistence paths ─────────────────────────────────────────────────────────
_APPDATA   = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "SteamGuard"
_CREDS_FILE  = _APPDATA / "loader_creds.bin"
_TOKEN_FILE  = _APPDATA / "loader_token.bin"
_SETTINGS_FILE = _APPDATA / "loader_settings.json"

# XOR fallback key (only used when DPAPI is unavailable, e.g. non-Windows/dev).
_XOR_KEY = b"RivvakSteamGuardLoader-v2-fallback-key-2026"


# ══════════════════════════════════════════════════════════════════════════════
#  QSS stylesheet (ImGui dark theme — verbatim spec)
# ══════════════════════════════════════════════════════════════════════════════
STYLESHEET = """
* { border-radius: 0px; outline: 0; font-family: 'Consolas', 'JetBrains Mono', monospace; font-size: 10pt; color: #F8FAFC; }
QMainWindow, QDialog, QWidget { background-color: #0B0D10; color: #F8FAFC; }
QFrame { background-color: #0B0D10; border: none; }
QFrame#Card { background-color: #11151B; border: 1px solid #2A3340; }
QFrame#TopPill { background-color: #11151B; border: 1px solid #2A3340; }
QLabel { background: transparent; color: #F8FAFC; }
QLabel#Muted { color: #94A3B8; }
QLabel#Title { color: #F8FAFC; font-weight: 700; font-size: 14pt; }
QLabel#CardTitle { color: #F8FAFC; font-weight: 700; font-size: 11pt; }
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #111E2E; color: #F8FAFC;
    border: 1px solid #2A3340; border-radius: 0px;
    padding: 6px 10px; font-size: 10pt; selection-background-color: #3B82F6;
}
QLineEdit:focus { border: 1px solid #3B82F6; }
QPushButton {
    background-color: #1E3A5F; color: #F8FAFC; border: 1px solid #2A3340;
    border-radius: 0px; padding: 6px 16px; font-size: 10pt; font-weight: 600;
    min-height: 28px;
}
QPushButton:hover { background-color: #3B82F6; color: #FFFFFF; border-color: #60A5FA; }
QPushButton:pressed { background-color: #2563EB; border-color: #2563EB; }
QPushButton:disabled { background-color: #151A21; color: #64748B; border-color: #222A35; }
QPushButton#CTA {
    background-color: #3B82F6; color: #FFFFFF; font-weight: 700;
    border: 1px solid #60A5FA; border-radius: 0px; padding: 8px 24px; min-height: 36px;
}
QPushButton#CTA:hover { background-color: #60A5FA; border-color: #93C5FD; }
QPushButton#CTA:pressed { background-color: #2563EB; border-color: #2563EB; }
QPushButton#Ghost {
    background-color: #11151B; color: #CBD5E1;
    border: 1px solid #2A3340; border-radius: 0px;
}
QPushButton#Ghost:hover { background-color: #172A45; color: #FFFFFF; border-color: #3B82F6; }
QCheckBox { color: #F8FAFC; font-size: 10pt; }
QCheckBox::indicator { width: 14px; height: 14px; background: #111E2E; border: 1px solid #2A3340; border-radius: 0px; }
QCheckBox::indicator:checked { background: #3B82F6; border-color: #60A5FA; }
QScrollBar:vertical { background: #0B0D10; width: 8px; border: none; }
QScrollBar::handle:vertical { background: #334155; border-radius: 0px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: #3B82F6; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: #0B0D10; }
QToolTip { background: #141922; color: #F8FAFC; border: 1px solid #2A3340; padding: 4px 8px; font-size: 9pt; }
"""


# ══════════════════════════════════════════════════════════════════════════════
#  Encryption helpers (DPAPI with XOR fallback)
# ══════════════════════════════════════════════════════════════════════════════

def _xor_bytes(data: bytes) -> bytes:
    key = _XOR_KEY
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _protect(data: bytes) -> bytes:
    """Encrypt bytes with Windows DPAPI (current-user). XOR fallback otherwise."""
    try:
        import win32crypt
        return b"DPAPI" + win32crypt.CryptProtectData(
            data, "SteamGuard-Loader", None, None, None, 0)
    except Exception:
        return b"XORv1" + _xor_bytes(data)


def _unprotect(blob: bytes) -> bytes:
    """Reverse of _protect(). Returns empty bytes on failure."""
    try:
        if blob.startswith(b"DPAPI"):
            import win32crypt
            _, dec = win32crypt.CryptUnprotectData(blob[5:], None, None, None, 0)
            return dec
        if blob.startswith(b"XORv1"):
            return _xor_bytes(blob[5:])
        # Legacy / unknown — try XOR anyway
        return _xor_bytes(blob)
    except Exception:
        return b""


def _ensure_appdata() -> None:
    try:
        _APPDATA.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def save_token(token: str) -> None:
    _ensure_appdata()
    try:
        _TOKEN_FILE.write_bytes(_protect(token.encode("utf-8")))
    except Exception:
        pass


def load_token() -> str:
    try:
        if _TOKEN_FILE.exists():
            return _unprotect(_TOKEN_FILE.read_bytes()).decode("utf-8", "ignore")
    except Exception:
        pass
    return ""


def save_creds(discord_id: str, key: str) -> None:
    """Store discord id + a hash of the key for auto-login."""
    _ensure_appdata()
    payload = json.dumps({
        "discord_user_id": discord_id,
        "key": key,
        "key_hash": hashlib.sha256(key.encode()).hexdigest(),
    }).encode("utf-8")
    try:
        _CREDS_FILE.write_bytes(_protect(payload))
    except Exception:
        pass


def load_creds() -> dict:
    try:
        if _CREDS_FILE.exists():
            raw = _unprotect(_CREDS_FILE.read_bytes())
            if raw:
                return json.loads(raw.decode("utf-8", "ignore"))
    except Exception:
        pass
    return {}


def clear_creds() -> None:
    for f in (_CREDS_FILE, _TOKEN_FILE):
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass


def load_settings() -> dict:
    defaults = {
        "launch_on_startup": False,
        "auto_launch_steamguard": False,
        "theme": "Dark",
    }
    try:
        if _SETTINGS_FILE.exists():
            defaults.update(json.loads(_SETTINGS_FILE.read_text("utf-8")))
    except Exception:
        pass
    return defaults


def save_settings(settings: dict) -> None:
    _ensure_appdata()
    try:
        _SETTINGS_FILE.write_text(json.dumps(settings, indent=2), "utf-8")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
#  Networking helpers
# ══════════════════════════════════════════════════════════════════════════════

def _http_json(url: str, method: str = "GET", payload: dict = None,
               token: str = "", timeout: int = 15) -> dict:
    """Blocking HTTP JSON request. MUST be called from a worker thread only."""
    headers = {"Content-Type": "application/json", "User-Agent": "SteamGuardLoader/2.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        import ssl
        ctx = ssl.create_default_context()
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        except (AttributeError, ValueError):
            pass
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "ignore")
            try:
                return json.loads(body) if body else {}
            except Exception:
                return {"error": "Invalid server response"}
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8", "ignore"))
            msg = detail.get("detail") or detail.get("error") or f"HTTP {e.code}"
        except Exception:
            msg = f"HTTP {e.code}"
        return {"error": msg, "status": e.code}
    except urllib.error.URLError as e:
        return {"error": f"Could not reach server: {e.reason}"}
    except TimeoutError:
        return {"error": "Server timed out. Check your connection."}
    except Exception as e:
        return {"error": f"Network error: {str(e)[:120]}"}


# ── Download worker with progress ─────────────────────────────────────────────

class DownloadWorker(QThread):
    """Downloads a file with progress reporting. Used to fetch SteamGuard.exe."""
    progress = pyqtSignal(int)       # 0-100
    done     = pyqtSignal(str)       # file path on success, empty string on error
    error    = pyqtSignal(str)       # error message

    def __init__(self, url: str, dest: Path, token: str = "", parent=None):
        super().__init__(parent)
        self.url   = url
        self.dest  = dest
        self.token = token

    def run(self):
        import urllib.request, urllib.error, ssl, socket
        headers = {
            "User-Agent":        "SteamGuardLoader/2.0",
            "X-Loader-Identity": LOADER_IDENTITY_TOKEN,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            req = urllib.request.Request(self.url, headers=headers)
            ctx = ssl.create_default_context()
            ensure_tools_dir()
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 65536  # 64KB chunks
                self.dest.parent.mkdir(parents=True, exist_ok=True)
                with open(self.dest, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            self.progress.emit(int(downloaded / total * 100))
            self.done.emit(str(self.dest))
        except urllib.error.HTTPError as e:
            # Emit a machine-readable category; the UI maps it to a message.
            if e.code == 404:
                self.error.emit("http_404")
            elif e.code == 401:
                self.error.emit("http_401")
            elif e.code == 403:
                self.error.emit("http_403")
            else:
                self.error.emit("generic")
        except socket.timeout:
            self.error.emit("timeout")
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                self.error.emit("timeout")
            else:
                self.error.emit("connection")
        except TimeoutError:
            self.error.emit("timeout")
        except Exception:
            self.error.emit("generic")


class Worker(QThread):
    """Generic worker that runs a callable off the UI thread and emits its result."""
    done = pyqtSignal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as e:  # never let a worker crash silently
            result = {"error": f"Worker error: {str(e)[:150]}"}
        self.done.emit(result)


class LoginWorker(QThread):
    """POSTs to /auth/login."""
    done = pyqtSignal(dict)

    def __init__(self, server_url: str, discord_id: str, key: str, parent=None):
        super().__init__(parent)
        self.server_url = server_url.rstrip("/")
        self.discord_id = discord_id
        self.key = key

    def run(self):
        result = _http_json(
            self.server_url + "/auth/login",
            method="POST",
            payload={"discord_user_id": self.discord_id, "key": self.key},
        )
        self.done.emit(result)


class GetJsonWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, url, token, parent=None):
        super().__init__(parent)
        self.url = url
        self.token = token

    def run(self):
        self.done.emit(_http_json(self.url, "GET", None, self.token))


class PostJsonWorker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, url, token, payload=None, parent=None):
        super().__init__(parent)
        self.url = url
        self.token = token
        self.payload = payload or {}

    def run(self):
        self.done.emit(_http_json(self.url, "POST", self.payload, self.token))


# ══════════════════════════════════════════════════════════════════════════════
#  QPainter icon library — all glyphs drawn, never loaded
# ══════════════════════════════════════════════════════════════════════════════

def _draw_gamepad(p, cx, cy, s, color):
    """Simple gamepad line-icon: rounded body + two circular buttons + stick dots."""
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    body = QRectF(cx - s * 0.6, cy - s * 0.32, s * 1.2, s * 0.72)
    p.drawRect(body)
    # antenna stub
    p.drawLine(int(cx), int(cy - s * 0.32), int(cx), int(cy - s * 0.52))
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    # left d-pad dots + right action dots
    dr = s * 0.09
    p.drawEllipse(QRectF(cx - s * 0.42 - dr, cy - dr, dr * 2, dr * 2))
    p.drawEllipse(QRectF(cx + s * 0.42 - dr, cy - dr, dr * 2, dr * 2))
    p.drawEllipse(QRectF(cx - dr, cy - s * 0.02 - dr, dr * 2, dr * 2))


def _draw_icon(p, kind, cx, cy, color, size=22):
    """Dispatch a nav / card glyph. All shapes drawn with QPainter."""
    col = QColor(color)
    pen = QPen(col, 2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    r = size / 2.0

    if kind == "logo":
        # White hexagon shield — RC logo
        p.setBrush(QBrush(QColor(255, 255, 255, 220)))
        p.setPen(Qt.NoPen)
        s = r * 0.8
        pts = []
        for i in range(6):
            angle = math.radians(i * 60 - 30)
            pts.append(QPoint(int(cx + s * math.cos(angle)),
                              int(cy + s * math.sin(angle))))
        p.drawPolygon(*pts)
        # "RC" text inside
        p.setPen(QColor(0, 0, 0, 200))
        f = QFont("Consolas", int(r * 0.35), QFont.Bold)
        p.setFont(f)
        p.drawText(QRect(int(cx - r), int(cy - r), int(r * 2), int(r * 2)),
                   Qt.AlignCenter, "RC")

    elif kind == "shield":
        # Hexagonal shield glyph (used on nav + cards) — square ImGui vibe
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        s = r * 0.9
        pts = []
        for i in range(6):
            angle = math.radians(i * 60 - 30)
            pts.append(QPoint(int(cx + s * math.cos(angle)),
                              int(cy + s * math.sin(angle))))
        p.drawPolygon(*pts)

    elif kind == "star":
        pts = QPolygonF()
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rad = r if i % 2 == 0 else r * 0.42
            pts.append(QPoint(int(cx + rad * math.cos(ang)),
                              int(cy + rad * math.sin(ang))))
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPolygon(pts)

    elif kind == "chain":
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(cx - r, cy - r * 0.42, r * 1.05, r * 0.84))
        p.drawRect(QRectF(cx - 0.05 * r, cy - r * 0.42, r * 1.05, r * 0.84))
        p.drawLine(int(cx - r * 0.28), int(cy), int(cx + r * 0.28), int(cy))

    elif kind == "gear":
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        teeth = QPolygonF()
        outer, inner = r, r * 0.7
        for i in range(12):
            ang = math.pi * i / 6.0
            rad = outer if i % 2 == 0 else inner
            teeth.append(QPoint(int(cx + rad * math.cos(ang)),
                                int(cy + rad * math.sin(ang))))
        p.drawPolygon(teeth)
        p.setBrush(QColor(BG))
        p.drawEllipse(QRectF(cx - r * 0.32, cy - r * 0.32, r * 0.64, r * 0.64))

    elif kind == "plus":
        pen2 = QPen(col, 2.2)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        p.drawLine(int(cx - r * 0.7), int(cy), int(cx + r * 0.7), int(cy))
        p.drawLine(int(cx), int(cy - r * 0.7), int(cx), int(cy + r * 0.7))

    elif kind == "logout":
        # Door + arrow icon — uses cx, cy, r passed into _draw_icon
        pen = QPen(QColor(255, 255, 255, 200), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        # Door outline (3 sides, open on right)
        p.drawLine(int(cx - r*0.6), int(cy - r*0.7), int(cx - r*0.6), int(cy + r*0.7))
        p.drawLine(int(cx - r*0.6), int(cy - r*0.7), int(cx + r*0.1), int(cy - r*0.7))
        p.drawLine(int(cx - r*0.6), int(cy + r*0.7), int(cx + r*0.1), int(cy + r*0.7))
        # Arrow pointing right
        p.drawLine(int(cx - r*0.1), int(cy), int(cx + r*0.7), int(cy))
        p.drawLine(int(cx + r*0.4), int(cy - r*0.3), int(cx + r*0.7), int(cy))
        p.drawLine(int(cx + r*0.4), int(cy + r*0.3), int(cx + r*0.7), int(cy))

    elif kind == "circle":
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(cx - r * 0.85, cy - r * 0.85, r * 1.7, r * 1.7))
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(cx - r * 0.28, cy - r * 0.28, r * 0.56, r * 0.56))

    elif kind == "roblox":
        p.setPen(QPen(col, 2))
        p.setBrush(Qt.NoBrush)
        side = r * 1.35
        p.save()
        p.translate(cx, cy)
        p.rotate(45)
        p.drawRect(QRectF(-side / 2, -side / 2, side, side))
        p.setBrush(col)
        hole = side * 0.22
        p.drawRect(QRectF(-hole / 2, -hole / 2, hole, hole))
        p.restore()

    elif kind == "terminal":
        p.setPen(QPen(col, 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(cx - r * 0.85, cy - r * 0.62, r * 1.7, r * 1.24))
        p.drawLine(int(cx - r * 0.52), int(cy - r * 0.18), int(cx - r * 0.24), int(cy))
        p.drawLine(int(cx - r * 0.52), int(cy + r * 0.18), int(cx - r * 0.24), int(cy))
        p.drawLine(int(cx), int(cy + r * 0.25), int(cx + r * 0.48), int(cy + r * 0.25))

    elif kind == "lock":
        p.setPen(Qt.NoPen)
        pen2 = QPen(col, 2.2)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(cx - r * 0.5, cy - r * 0.9, r, r * 0.9), 0, 180 * 16)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRect(QRectF(cx - r * 0.65, cy - r * 0.15, r * 1.3, r))
        p.setBrush(QColor(BG))
        p.drawEllipse(QRectF(cx - r * 0.12, cy + r * 0.18, r * 0.24, r * 0.24))

    elif kind == "clock":
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(cx - r * 0.85, cy - r * 0.85, r * 1.7, r * 1.7))
        p.drawLine(int(cx), int(cy), int(cx), int(cy - r * 0.5))
        p.drawLine(int(cx), int(cy), int(cx + r * 0.4), int(cy))


# ══════════════════════════════════════════════════════════════════════════════
#  Small painter-drawn widgets
# ══════════════════════════════════════════════════════════════════════════════

class IconWidget(QWidget):
    """A tiny fixed-size widget that paints a single QPainter glyph."""

    def __init__(self, kind, color=TEXT, size=20, box=None, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._color = color
        self._gsize = size
        box = box or (size + 6)
        self.setFixedSize(box, box)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_color(self, color):
        self._color = color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        _draw_icon(p, self._kind, self.width() / 2, self.height() / 2,
                   self._color, self._gsize)
        p.end()


class Spinner(QWidget):
    """Two concentric rotating arcs — outer green (#35B18E), inner blue (#4296FA).

    Stroke width scaled with the widget. Animated ~60fps.
    """

    def __init__(self, size=80, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self.show()
        self._timer.start(16)   # ~60fps

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        stroke = max(3, int(self._size * 0.15))   # ~12px at 80px
        m = stroke / 2 + 1
        # Outer arc
        pen = QPen(QColor(SPINNER_GREEN), stroke)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        outer = QRectF(m, m, self._size - 2 * m, self._size - 2 * m)
        p.drawArc(outer, -self._angle * 16, 110 * 16)
        # Inner blue arc
        pen2 = QPen(QColor(SPINNER_BLUE), stroke)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        gap = stroke * 1.6
        inner = QRectF(m + gap, m + gap,
                       self._size - 2 * (m + gap), self._size - 2 * (m + gap))
        p.drawArc(inner, (-self._angle - 140) * 16, 90 * 16)
        p.end()


class Avatar(QWidget):
    """Square avatar with the first letter of the username (accent)."""

    def __init__(self, letter="?", size=32, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._letter = (letter or "?")[0].upper()
        self._size = size

    def set_letter(self, letter):
        self._letter = (letter or "?")[0].upper()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(QColor(FRAME_BG))
        p.drawRect(0, 0, self._size - 1, self._size - 1)
        p.setPen(QColor(ACCENT))
        f = QFont("Consolas", int(self._size * 0.42))
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self._letter)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  Sidebar (ImGui icon-only nav rail)
# ══════════════════════════════════════════════════════════════════════════════

class LogoSlot(QWidget):
    """Top brand slot: 72x56 outer footprint, real RC logo PNG centered inside.

    Loads the highest-res brand asset available (icon_256.png, falling back
    to icon.png then rc_logo_64.png) into a child QLabel and applies the same
    soft blue glow used on the login card (see commit 5767535) so the logo
    reads clearly against the dark sidebar instead of the old QPainter-drawn
    hexagon placeholder.
    """

    LOGO_PX = 40

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(72, 56)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        logo = QLabel(self)
        logo.setFixedSize(self.LOGO_PX, self.LOGO_PX)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("background:transparent;")

        for logo_src in (LOGO_PATH, LOGO_PATH_ALT, LOGO_PATH_LEG):
            if logo_src.exists():
                pm = QPixmap(str(logo_src))
                if not pm.isNull():
                    # Render at 2x then downscale for crisper edges.
                    logo.setPixmap(pm.scaled(
                        self.LOGO_PX * 2, self.LOGO_PX * 2,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    ).scaled(
                        self.LOGO_PX, self.LOGO_PX,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    ))
                    break

        # Soft glow so the logo separates from the dark sidebar background.
        glow = QGraphicsDropShadowEffect(logo)
        glow.setBlurRadius(22)
        glow.setOffset(0, 0)
        glow.setColor(QColor(66, 150, 250, 180))  # ImGui accent blue @ 70% alpha
        logo.setGraphicsEffect(glow)

        lay.addWidget(logo, 0, Qt.AlignHCenter | Qt.AlignVCenter)


class SideIconButton(QPushButton):
    """A 72x56 flat nav slot with a QPainter glyph.

    Active: HEADER bg + 3px ACCENT left border + white icon.
    Hover:  NAV_HOVER bg + white icon.
    Idle:   transparent + grey icon.
    """

    def __init__(self, kind, tooltip="", parent=None):
        super().__init__(parent)
        self._kind = kind
        self._active = False
        self._hover = False
        self.setCheckable(kind not in ("plus", "logout"))
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(72, 56)
        self.setToolTip(tooltip)
        self.setStyleSheet("QPushButton { background:transparent; border:none; }")

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def setChecked(self, val):
        super().setChecked(val)
        self._active = val
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        if self._active:
            p.setBrush(QColor(HEADER))
            p.drawRect(0, 0, w, h)
            # 3px accent left border
            p.setBrush(QColor(ACCENT))
            p.drawRect(0, 0, 3, h)
            icon_col = TEXT
        elif self._hover:
            p.setBrush(QColor(NAV_HOVER))
            p.drawRect(0, 0, w, h)
            icon_col = TEXT
        else:
            icon_col = ICON_GREY
        if self._kind in ("plus", "logout"):
            icon_col = QColor(255, 255, 255, 166)
        _draw_icon(p, self._kind, w / 2, h / 2, icon_col, 22)
        p.end()


class Sidebar(QFrame):
    """72px-wide ImGui nav rail. TITLE_BG fill, 1px right border, logo at top,
    checkable icon buttons, logout at the bottom. No corner radius."""

    def __init__(self, on_nav, on_logout, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(72)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "QFrame#Sidebar { background-color:%s; border:none;"
            " border-right:1px solid %s; }" % (TITLE_BG, BORDER))
        self._on_nav = on_nav
        self._active_index = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignHCenter)

        # Top brand logo slot
        lay.addWidget(LogoSlot(), alignment=Qt.AlignHCenter)
        lay.addSpacing(16)

        # Nav buttons: Home(shield), Rewards(star), Referrals(chain), Settings(gear)
        self._nav = []
        for kind, tip, idx in [
            ("shield", "Popular products", 0),
            ("star", "Rewards", 1),
            ("chain", "Referrals", 2),
            ("gear", "Settings", 3),
        ]:
            btn = SideIconButton(kind, tip)
            btn.clicked.connect(lambda _=False, i=idx: self._select(i))
            lay.addWidget(btn, alignment=Qt.AlignHCenter)
            self._nav.append(btn)
        self._nav[0].setChecked(True)

        lay.addStretch(1)

        # Bottom logout button (arrow-out icon)
        self._logout_btn = SideIconButton("logout", "Logout")
        self._logout_btn.setToolTip("Logout")
        self._logout_btn.clicked.connect(on_logout)
        lay.addWidget(self._logout_btn, alignment=Qt.AlignHCenter)

    def _select(self, idx):
        self._active_index = idx
        for i, b in enumerate(self._nav):
            b.setChecked(i == idx)
        self.update()
        self._on_nav(idx)

    def set_active(self, idx):
        self._active_index = idx
        for i, b in enumerate(self._nav):
            b.setChecked(i == idx)
        self.update()


# ══════════════════════════════════════════════════════════════════════════════
#  Product cards — painted gradient art, no images
# ══════════════════════════════════════════════════════════════════════════════

class ProductCard(QFrame):
    """A "Popular products" product card, ImGui-styled:

    - Top image area (~50% height) painted as a QPainter linear gradient with a
      subtle, clipped stylized tool name.
    - Bottom section: logo glyph + tool name, optional activation date row, and a
      full-width action button.
    - 1px BORDER, ChildBg base, ZERO corner radius.
    """

    def __init__(self, title, glyph, grad_top, grad_bottom, action_text,
                 action_enabled=True, activated_date=None, on_action=None,
                 tagline="", status_text="", blue_glow=False, parent=None):
        super().__init__(parent)
        self._title = title
        self._glyph = glyph
        self._grad_top = QColor(grad_top)
        self._grad_bottom = QColor(grad_bottom)
        self._activated_date = activated_date
        self._tagline = tagline
        self._status_text = status_text
        self._hover = False
        self.setMouseTracking(True)
        self.setObjectName("Card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(340)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)

        # spacer for the painted image header (~50%)
        lay.addStretch(1)

        # bottom info block
        bottom = QWidget()
        bottom.setStyleSheet("background:transparent;")
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(14, 12, 14, 14)
        bl.setSpacing(10)

        # logo + name row
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.setContentsMargins(0, 0, 0, 0)
        icon = IconWidget(glyph, TEXT, 20, box=24)
        if blue_glow:
            # Consistent blue brand glow (slightly softer than the RC login logo).
            glow = QGraphicsDropShadowEffect(icon)
            glow.setBlurRadius(20)
            glow.setOffset(0, 0)
            glow.setColor(QColor(66, 150, 250, 120))
            icon.setGraphicsEffect(glow)
        name_row.addWidget(icon, alignment=Qt.AlignVCenter)
        name_lbl = QLabel(title)
        _name_color = TEXT_DIM if blue_glow else TEXT
        name_lbl.setStyleSheet(
            f"color:{_name_color}; font-size:12pt; font-weight:700; background:transparent;")
        name_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        name_lbl.setTextInteractionFlags(Qt.NoTextInteraction)
        name_lbl.setWordWrap(True)
        name_lbl.setMinimumHeight(44)   # room for a wrapped second line
        name_lbl.setToolTip(title)
        self._name_lbl = name_lbl
        name_row.addWidget(name_lbl, 1)
        bl.addLayout(name_row)

        if tagline:
            tag_lbl = QLabel(tagline)
            tag_lbl.setStyleSheet(
                f"color:{TEXT_DIM}; font-size:8.5pt; background:transparent;")
            tag_lbl.setWordWrap(False)
            tag_lbl.setToolTip(tagline)
            self._tag_lbl = tag_lbl
            bl.addWidget(tag_lbl)
        else:
            self._tag_lbl = None

        if status_text:
            status_lbl = QLabel(status_text.upper())
            status_lbl.setStyleSheet(
                f"QLabel {{ color:{ACCENT}; border:1px solid {ACCENT_DIM}; background:{BG}; padding:2px 6px; font-size:8pt; font-weight:700; }}")
            status_lbl.setFixedHeight(22)
            bl.addWidget(status_lbl, alignment=Qt.AlignLeft)

        # activation date row
        self._date_val = None
        if activated_date is not None:
            date_row = QHBoxLayout()
            date_row.setSpacing(8)
            date_row.setContentsMargins(0, 0, 0, 0)
            act_lbl = QLabel("Activated:")
            act_lbl.setObjectName("Muted")
            act_lbl.setStyleSheet(
                f"color:{TEXT_DIM}; font-size:9pt; background:transparent;")
            date_row.addWidget(act_lbl)
            date_row.addStretch(1)
            clock = IconWidget("clock", TEXT_DIM, 13, box=16)
            date_row.addWidget(clock, alignment=Qt.AlignVCenter)
            dval = QLabel(activated_date or "—")
            dval.setStyleSheet(
                f"color:{TEXT}; font-size:9pt; font-weight:600; background:transparent;")
            date_row.addWidget(dval)
            self._date_val = dval
            bl.addLayout(date_row)

        # action button (full width, ImGui CTA / Ghost style)
        self.action_btn = QPushButton(action_text)
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.setFixedHeight(40)
        self._enabled = action_enabled
        if action_enabled:
            self.action_btn.setObjectName("CTA")
        else:
            self.action_btn.setObjectName("Ghost")
        if action_enabled and on_action is not None:
            self.action_btn.clicked.connect(on_action)
        if not action_enabled:
            self.action_btn.setDisabled(True)
        bl.addWidget(self.action_btn)

        lay.addWidget(bottom)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def _apply_elide(self):
        # The title now word-wraps instead of eliding; only the tagline elides.
        for attr, full in (("_tag_lbl", self._tagline),):
            lbl = getattr(self, attr, None)
            if lbl is not None:
                metrics = QFontMetrics(lbl.font())
                lbl.setText(metrics.elidedText(full, Qt.ElideRight, max(40, lbl.width())))

    def resizeEvent(self, event):
        self._apply_elide()
        super().resizeEvent(event)

    # helper to update the activation date from server data
    def set_activated_date(self, text):
        if self._date_val is not None:
            self._date_val.setText(text)

    # helpers used by the preserved download/launch flow
    def set_action_label(self, text):
        self.action_btn.setText(text)

    def set_action_running(self):
        self.action_btn.setText("Running")
        self.action_btn.setDisabled(True)

    def reset_action(self, text, _color=None):
        self.action_btn.setText(text)
        self.action_btn.setDisabled(False)

    def paintEvent(self, _):
        # Draw the painted art header first, then let the QSS border draw on top.
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Card base (ChildBg), leaving 1px for the QSS border
        p.fillRect(QRect(1, 1, self.width() - 2, self.height() - 2), QColor(BG_CHILD))

        # Top image area gradient (~50%)
        img_h = int(self.height() * 0.50)
        art_rect = QRect(1, 1, self.width() - 2, img_h - 1)
        grad = QLinearGradient(0, 0, self.width(), img_h)
        grad.setColorAt(0, self._grad_top.lighter(112 if self._hover else 100))
        grad.setColorAt(1, self._grad_bottom)
        p.fillRect(art_rect, QBrush(grad))

        # ImGui-style scan lines / corner grid, clipped to header only.
        p.save()
        p.setClipRect(art_rect)
        p.setOpacity(0.10 if not self._hover else 0.16)
        p.setPen(QPen(QColor(255, 255, 255), 1))
        step = 18
        for x in range(art_rect.left() - img_h, art_rect.right() + img_h, step):
            p.drawLine(x, art_rect.bottom(), x + img_h, art_rect.top())
        p.restore()

        # Accent glyph plate.
        p.save()
        p.setOpacity(0.92)
        p.setPen(QPen(QColor(ACCENT if self._hover else BORDER), 1))
        p.setBrush(QColor(8, 10, 13, 150))
        plate = QRectF(18, 18, 54, 54)
        p.drawRect(plate)
        _draw_icon(p, self._glyph, plate.center().x(), plate.center().y(), ACCENT if self._hover else TEXT, 26)
        p.restore()

        # Large, subtle stylized tool name over the gradient.
        # Auto-shrink the font so multi-word titles ("Roblox Copier",
        # "Coming Soon") never clip mid-word at any card width.
        p.save()
        p.setClipRect(art_rect)
        text_rect = QRect(14, 14, self.width() - 28, img_h - 28)
        font = QFont("Consolas", 1)
        font.setWeight(QFont.DemiBold)
        # Start at 42px and step down until the full title fits horizontally.
        for size_px in (42, 38, 34, 30, 26, 22, 18):
            font.setPixelSize(size_px)
            fm = QFontMetrics(font)
            if fm.horizontalAdvance(self._title) <= text_rect.width():
                break
        p.setFont(font)
        p.setOpacity(0.12)
        p.setPen(QColor(255, 255, 255))
        # TextDontClip so any leftover overhang still renders instead of ellipsizing.
        p.drawText(text_rect,
                   Qt.AlignBottom | Qt.AlignLeft | Qt.TextDontClip,
                   self._title)
        p.restore()

        # Hairline under the art area
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawLine(1, img_h, self.width() - 1, img_h)

        # 1px border (QSS #Card also styles this, drawn here for the painted overlay)
        p.setPen(QPen(QColor(ACCENT if self._hover else BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        if self._hover:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawRect(0, 0, self.width(), 2)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  Login card (ImGui dark card over a dimmed background)
# ══════════════════════════════════════════════════════════════════════════════

class LoginScreen(QWidget):
    login_success = pyqtSignal(dict)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._win = window
        self._worker = None
        self._settings = load_settings()
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        card = self._build_card()
        self._card = card
        card.setParent(self)

    def _build_card(self):
        card = QFrame()
        card.setObjectName("Card")
        card.setFixedWidth(460)
        card.setAttribute(Qt.WA_StyledBackground, True)

        c = QVBoxLayout(card)
        c.setContentsMargins(40, 28, 40, 32)
        c.setSpacing(0)

        # Brand row: RC logo (top-left) + title text next to it
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(10)

        # RC logo — 48px with a soft blue-white glow so the white outline
        # reads against the near-black card. Loads the highest-res source
        # available and scales down with smooth transform for crisp edges.
        LOGO_PX = 48
        logo = QLabel()
        logo.setFixedSize(LOGO_PX, LOGO_PX)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet("background:transparent;")
        for logo_src in (LOGO_PATH, LOGO_PATH_ALT, LOGO_PATH_LEG):
            if logo_src.exists():
                pm = QPixmap(str(logo_src))
                if not pm.isNull():
                    # Render at 2x then downscale for crisper edges.
                    logo.setPixmap(pm.scaled(
                        LOGO_PX * 2, LOGO_PX * 2,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    ).scaled(
                        LOGO_PX, LOGO_PX,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    ))
                    break

        # Soft glow so the outline logo separates from the dark card.
        glow = QGraphicsDropShadowEffect(logo)
        glow.setBlurRadius(22)
        glow.setOffset(0, 0)
        glow.setColor(QColor(66, 150, 250, 180))  # ImGui accent blue @ 70% alpha
        logo.setGraphicsEffect(glow)

        brand_row.addWidget(logo, 0, Qt.AlignVCenter)

        title = QLabel("RIVVAK COMMUNITY")
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        title.setStyleSheet(
            f"color:{TEXT}; font-size:14pt; font-weight:700; background:transparent; letter-spacing:1px;")
        brand_row.addWidget(title, 1, Qt.AlignVCenter)

        c.addLayout(brand_row)
        c.addSpacing(6)

        # Subtitle
        sub = QLabel("Login to access your tools")
        sub.setObjectName("Muted")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:10pt; background:transparent;")
        c.addWidget(sub)
        c.addSpacing(20)

        # Input 1 — Discord ID
        self._discord = QLineEdit()
        self._discord.setPlaceholderText("Discord ID")
        self._discord.setFixedHeight(38)
        c.addWidget(self._discord)
        c.addSpacing(10)

        # Input 2 — License Key (masked)
        self._key = QLineEdit()
        self._key.setPlaceholderText("License Key")
        self._key.setEchoMode(QLineEdit.Password)
        self._key.setFixedHeight(38)
        c.addWidget(self._key)
        c.addSpacing(10)

        # Remember me + error row
        self._remember = QCheckBox("Remember me")
        self._remember.setCursor(Qt.PointingHandCursor)
        c.addWidget(self._remember, alignment=Qt.AlignLeft)

        self._error = QLabel("")
        self._error.setAlignment(Qt.AlignCenter)
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color:{RED}; font-size:9pt; background:transparent;")
        self._error.hide()
        c.addWidget(self._error)
        c.addSpacing(8)

        # LOGIN button — ImGui blue CTA
        self._login_btn = QPushButton("LOGIN")
        self._login_btn.setObjectName("CTA")
        self._login_btn.setCursor(Qt.PointingHandCursor)
        self._login_btn.setFixedHeight(40)
        self._login_btn.clicked.connect(self._on_login)
        c.addWidget(self._login_btn)
        c.addSpacing(16)

        # Social buttons row (Discord | YouTube) — Ghost style with brand icons
        social = QHBoxLayout()
        social.setSpacing(10)
        social.setContentsMargins(0, 0, 0, 0)

        discord_btn = QPushButton("  Discord")
        discord_btn.setObjectName("Ghost")
        discord_btn.setCursor(Qt.PointingHandCursor)
        discord_btn.setFixedHeight(34)
        discord_btn.setIcon(_load_icon(DISCORD_SVG, DISCORD_PNG))
        discord_btn.setIconSize(QSize(18, 18))
        discord_btn.clicked.connect(lambda: webbrowser.open(DISCORD_INVITE))

        youtube_btn = QPushButton("  YouTube")
        youtube_btn.setObjectName("Ghost")
        youtube_btn.setCursor(Qt.PointingHandCursor)
        youtube_btn.setFixedHeight(34)
        youtube_btn.setIcon(_load_icon(YOUTUBE_SVG, YOUTUBE_PNG))
        youtube_btn.setIconSize(QSize(18, 18))
        youtube_btn.clicked.connect(lambda: webbrowser.open(BRAND_SITE))

        social.addWidget(discord_btn, 1)
        social.addWidget(youtube_btn, 1)
        c.addLayout(social)
        c.addSpacing(10)

        # Footer links
        footer = QHBoxLayout()
        footer.setSpacing(16)
        join = self._link("Join Discord", lambda: webbrowser.open(DISCORD_INVITE))
        getkey = self._link("Get a Key", lambda: webbrowser.open(BRAND_SITE))
        footer.addStretch(1)
        footer.addWidget(join)
        footer.addWidget(getkey)
        footer.addStretch(1)
        c.addLayout(footer)

        # Prefill saved creds
        creds = load_creds()
        if creds:
            self._discord.setText(creds.get("discord_user_id", ""))
            self._key.setText(creds.get("key", ""))
            self._remember.setChecked(True)

        return card

    def _link(self, text, slot):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFlat(True)
        b.setStyleSheet(
            "QPushButton { background:transparent; color:" + ACCENT + ";"
            " border:none; min-height:0; padding:0; font-size:9pt; }"
            " QPushButton:hover { color:#60A5FA; }")
        b.clicked.connect(slot)
        return b

    def resizeEvent(self, e):
        cw, ch = self._card.width(), self._card.height()
        self._card.move((self.width() - cw) // 2, (self.height() - ch) // 2)
        self._card.raise_()
        super().resizeEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # base background
        p.fillRect(self.rect(), QColor(BG))
        # dim overlay
        p.fillRect(self.rect(), QColor(10, 10, 10, int(0.6 * 255)))
        p.end()

    def try_auto_login(self):
        creds = load_creds()
        if creds.get("discord_user_id") and creds.get("key"):
            self._on_login(auto=True)

    def _set_busy(self, busy: bool):
        self._login_btn.setDisabled(busy)
        self._login_btn.setText("SIGNING IN..." if busy else "LOGIN")
        if busy:
            self._error.hide()

    def _show_error(self, msg: str):
        self._error.setText(msg)
        self._error.show()

    def _on_login(self, auto=False):
        discord_id = self._discord.text().strip()
        key = self._key.text().strip()
        if not discord_id:
            self._show_error("Please enter your Discord ID.")
            return
        if not key:
            self._show_error("Please enter your license key.")
            return

        self._set_busy(True)
        server = DEFAULT_SERVER_URL
        self._worker = LoginWorker(server, discord_id, key)
        self._worker.done.connect(lambda r: self._on_login_done(r, discord_id, key))
        self._worker.start()

    def _on_login_done(self, result: dict, discord_id: str, key: str):
        self._set_busy(False)
        if not isinstance(result, dict) or result.get("error") or not (
                result.get("token") or result.get("session_token") or result.get("valid")):
            msg = (result or {}).get("error", "Login failed. Check your credentials.")
            self._show_error(msg)
            return

        token = result.get("token") or result.get("session_token") or ""
        if token:
            save_token(token)

        if self._remember.isChecked():
            save_creds(discord_id, key)
        else:
            clear_creds()

        session = {
            "token": token,
            "discord_user_id": discord_id,
            "key": key,
            "discord_username": result.get("discord_username")
            or result.get("username") or f"User {discord_id[:6]}",
            "tier": (result.get("tier") or ("PREMIUM" if result.get("premium") else "FREE")).upper(),
            "server_url": DEFAULT_SERVER_URL,
        }
        self.login_success.emit(session)


# ══════════════════════════════════════════════════════════════════════════════
#  Dashboard (sidebar + stacked tabs)
# ══════════════════════════════════════════════════════════════════════════════

class Dashboard(QWidget):
    logout = pyqtSignal()

    def __init__(self, window, session: dict, parent=None):
        super().__init__(parent)
        self._win = window
        self._session = session
        self._token = session.get("token", "")
        self._server = DEFAULT_SERVER_URL.rstrip("/")
        self._settings = load_settings()
        self._processes = {}
        self._sg_process = None
        self._roblox_process = None
        self._proc_timer = QTimer(self)
        self._proc_timer.timeout.connect(self._check_processes)
        self._workers = []   # keep references alive
        self._referral_ensured = False
        self._tier_lbl = None
        self._sg_card_date = None
        self._dl_attempts = 0
        self._dl_error_banner = None
        self._dl_error_lbl = None
        self._build()
        self._load_overview()

    def _build(self):
        main = QHBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        self._sidebar = Sidebar(self._switch_tab, self._on_logout)
        main.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background:{BG};")
        self._stack.addWidget(self._build_products_tab())    # 0
        self._stack.addWidget(self._build_rewards_tab())      # 1
        self._stack.addWidget(self._build_referrals_tab())    # 2
        self._stack.addWidget(self._build_settings_tab())     # 3
        main.addWidget(self._stack, 1)

    def _switch_tab(self, idx):
        self._sidebar.set_active(idx)
        self._stack.setCurrentIndex(idx)
        if idx == 1:
            self._load_rewards()
        elif idx == 2:
            self._load_referrals()

    def _scroll_page(self):
        """A scrollable page over the app background. Returns (scroll, inner_layout)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"QScrollArea {{ border:none; background:{BG}; }}")
        scroll.viewport().setStyleSheet(f"background:{BG};")
        inner = QWidget()
        inner.setStyleSheet(f"background:{BG};")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(32, 28, 32, 28)
        lay.setSpacing(16)
        scroll.setWidget(inner)
        return scroll, lay

    def _header(self, text):
        h = QLabel(text)
        h.setObjectName("Title")
        h.setStyleSheet(
            f"color:{TEXT}; font-size:16pt; font-weight:700; background:transparent;")
        return h

    # ── Products tab ("Popular products") ─────────────────────────────────
    def _build_products_tab(self):
        scroll, lay = self._scroll_page()

        # Header row with tier + time labels
        head_row = QHBoxLayout()
        head_row.addWidget(self._header("Popular products"))
        head_row.addStretch(1)
        self._time_lbl = QLabel("")
        self._time_lbl.setObjectName("Muted")
        self._time_lbl.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:9pt; background:transparent;")
        self._tier_lbl = QLabel("")
        self._tier_lbl.setStyleSheet(
            f"color:{ACCENT}; font-size:10pt; font-weight:700; background:transparent;")
        pill = QFrame()
        pill.setObjectName("TopPill")
        pill.setAttribute(Qt.WA_StyledBackground, True)
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(10, 4, 10, 4)
        pl.setSpacing(10)
        pl.addWidget(self._time_lbl)
        pl.addWidget(self._tier_lbl)
        head_row.addWidget(pill, alignment=Qt.AlignVCenter)
        lay.addLayout(head_row)

        # Dedicated download-error banner (hidden until a download fails 3x).
        lay.addWidget(self._build_dl_error_banner())

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)

        # Card 1 — SteamGuard (dark green), activated + Launch
        self._sg_card = ProductCard(
            "SteamGuard", "shield",
            grad_top="#1a3a28", grad_bottom="#0d1a14",
            action_text="Launch", action_enabled=True,
            activated_date="—",
            on_action=self._on_launch,
            tagline="Steam session utility", status_text="Installed on demand",
        )
        self._sg_card_date = self._sg_card
        grid.addWidget(self._sg_card, 0, 0)

        # Card 2 — Roblox Copier (bundled Python local companion)
        self._roblox_card = ProductCard(
            "Roblox Copier", "roblox",
            grad_top="#17315F", grad_bottom="#0C1024",
            action_text="Launch", action_enabled=True,
            on_action=self._on_launch_roblox_copy,
            tagline="Local Studio animation copier", status_text="Bundled",
        )
        grid.addWidget(self._roblox_card, 0, 1)

        # Card 3 — Coming Soon (dark grey)
        self._soon_card = ProductCard(
            "Coming Soon", "lock",
            grad_top="#17223A", grad_bottom="#0C1018",
            action_text="Coming Soon", action_enabled=False,
            tagline="More tools are being prepared", status_text="Locked",
            blue_glow=True,
        )
        grid.addWidget(self._soon_card, 0, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)
        lay.addStretch(1)

        return scroll

    def _find_roblox_copy_tool(self):
        """Return a command for the bundled Roblox Copy Tool."""
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        candidates = [
            # Current layout (Nuitka build in tools/roblox_copy_tool/).
            base / "tools" / "roblox_copy_tool" / "RobloxCopyTool.exe",
            ROBLOX_COPIER_EXE,
            # Legacy fallbacks so older bundles keep working.
            base / "roblox_copier.exe",
            base / "dist" / "roblox_copier.exe",
            ROBLOX_COPIER_EXE_LEGACY,
            Path(__file__).resolve().parent / "dist" / "roblox_copier.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return [str(candidate)]
        # Dev fallback: run the .py entrypoint directly.
        if not getattr(sys, "frozen", False):
            if ROBLOX_COPY_TOOL_ENTRY.exists():
                return [sys.executable, str(ROBLOX_COPY_TOOL_ENTRY)]
            if ROBLOX_COPIER_PKG.exists():
                return [sys.executable, "-m", "roblox_copier"]
        return None

    def _on_launch_roblox_copy(self):
        cmd = self._find_roblox_copy_tool()
        if not cmd:
            self._time_lbl.setText("Roblox copier missing")
            return
        self._launch_tool("roblox", cmd, self._roblox_card)

    def _find_steamguard(self):
        """Look for SteamGuard.exe: local cache first, then same folder, then PATH."""
        cached = get_local_tool_path("SteamGuard.exe")
        if cached.exists():
            return str(cached)
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
        local = base / "SteamGuard.exe"
        if local.exists():
            return str(local)
        import shutil
        found = shutil.which("SteamGuard.exe")
        if found:
            return found
        return None

    def _on_launch(self):
        exe = self._find_steamguard()
        if exe:
            self._launch_exe(exe)
            return
        self._start_download()

    def _build_dl_error_banner(self):
        """Dedicated failure banner, separate from the tier pill. Hidden by default."""
        banner = QFrame()
        banner.setObjectName("DlErrorBanner")
        banner.setAttribute(Qt.WA_StyledBackground, True)
        banner.setStyleSheet(
            "QFrame#DlErrorBanner { background-color:%s; "
            "border:1px solid %s; border-left:3px solid #EF4444; }" % (BG_CHILD, BORDER))
        row = QHBoxLayout(banner)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(10)

        icon = QLabel()
        pm = _load_icon(WARNING_SVG).pixmap(24, 24)
        if pm.isNull():
            icon.setText("⚠")
            icon.setStyleSheet("color:#EF4444; font-size:14pt; background:transparent;")
        else:
            icon.setPixmap(pm)
            icon.setFixedSize(24, 24)
        icon.setStyleSheet(icon.styleSheet() + "background:transparent;")
        row.addWidget(icon, 0, Qt.AlignVCenter)

        self._dl_error_lbl = QLabel("")
        self._dl_error_lbl.setWordWrap(True)
        self._dl_error_lbl.setAlignment(Qt.AlignCenter)
        self._dl_error_lbl.setStyleSheet(
            "color:#EF4444; font-size:9.5pt; font-weight:600; background:transparent;")
        row.addWidget(self._dl_error_lbl, 1, Qt.AlignVCenter)

        retry_btn = QPushButton("Retry")
        retry_btn.setObjectName("Ghost")
        retry_btn.setCursor(Qt.PointingHandCursor)
        retry_btn.setFixedHeight(28)
        retry_btn.clicked.connect(self._on_dl_retry_clicked)
        row.addWidget(retry_btn, 0, Qt.AlignVCenter)

        banner.hide()
        self._dl_error_banner = banner
        return banner

    def _on_dl_retry_clicked(self):
        """Manual retry from the banner: reset the attempt counter and restart."""
        self._dl_attempts = 0
        if self._dl_error_banner is not None:
            self._dl_error_banner.hide()
        self._start_download()

    def _start_download(self, retry: bool = False):
        """Download SteamGuard.exe from the server with a progress indicator."""
        if not retry:
            self._dl_attempts = 0
            if self._dl_error_banner is not None:
                self._dl_error_banner.hide()
        self._sg_card.set_action_label("Downloading...")
        token = self._token or ""
        dest  = get_local_tool_path("SteamGuard.exe")
        self._dl_worker = DownloadWorker(STEAMGUARD_DOWNLOAD_URL, dest, token, self)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.done.connect(self._on_dl_done)
        self._dl_worker.error.connect(self._on_dl_error)
        self._workers.append(self._dl_worker)
        self._dl_worker.start()

    def _on_dl_progress(self, pct: int):
        self._sg_card.set_action_label(f"Downloading {pct}%")

    def _on_dl_done(self, path: str):
        # Only fires on success now (the worker no longer emits done("") on error).
        if not path:
            return
        self._dl_attempts = 0
        if self._dl_error_banner is not None:
            self._dl_error_banner.hide()
        self._sg_card.reset_action("Launch", ACCENT)
        self._launch_exe(path)

    def _on_dl_error(self, category: str):
        # Silent auto-retry (up to 3 attempts) before showing any error UI.
        if self._dl_attempts < 3:
            self._dl_attempts += 1
            self._sg_card.set_action_label("Retrying...")
            QTimer.singleShot(1500, lambda: self._start_download(retry=True))
            return
        # All retries exhausted — surface a specific message in the banner.
        messages = {
            "http_404":   "Download unavailable — the file was moved or removed.",
            "http_401":   "Access denied — your license may need renewal.",
            "http_403":   "Access denied — your license may need renewal.",
            "timeout":    "Server took too long to respond.",
            "connection": "Can't reach the download server — check your internet.",
        }
        msg = messages.get(category, "Download failed after 3 attempts. Try again in a moment.")
        self._sg_card.reset_action("Launch", ACCENT)
        if self._dl_error_lbl is not None:
            self._dl_error_lbl.setText(msg)
        if self._dl_error_banner is not None:
            self._dl_error_banner.show()

    def _launch_exe(self, exe):
        """Launch SteamGuard.exe and start monitoring the process."""
        self._launch_tool("steamguard", [str(exe)], self._sg_card)

    def _launch_tool(self, key, cmd, card):
        if key in self._processes and self._processes[key].poll() is None:
            self._time_lbl.setText("Already running")
            return
        try:
            if len(cmd) >= 3 and cmd[1] == "-m":
                cwd = str(Path(__file__).resolve().parent)
            else:
                cwd = str(Path(cmd[-1]).resolve().parent) if cmd[-1] else None
            flags = 0
            if os.name == "nt":
                flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            proc = subprocess.Popen(cmd, cwd=cwd, creationflags=flags)
        except Exception:
            self._time_lbl.setText("Launch failed")
            return
        self._processes[key] = proc
        if key == "steamguard":
            self._sg_process = proc
        elif key == "roblox":
            self._roblox_process = proc
        card.set_action_running()
        self._time_lbl.setText(f"{key.title()} running")
        self._proc_timer.start(1000)

    def _check_processes(self):
        if not self._processes:
            self._proc_timer.stop()
            return
        finished = []
        for key, proc in list(self._processes.items()):
            if proc.poll() is not None:
                finished.append(key)
        for key in finished:
            self._processes.pop(key, None)
            if key == "steamguard":
                self._sg_process = None
                self._sg_card.reset_action("Launch", ACCENT)
            elif key == "roblox":
                self._roblox_process = None
                self._roblox_card.reset_action("Launch", ACCENT)
        if not self._processes:
            self._proc_timer.stop()

    def _load_overview(self):
        if not self._token:
            return
        worker = GetJsonWorker(self._server + "/me/overview", self._token)
        worker.done.connect(self._on_overview)
        self._workers.append(worker)
        worker.start()

    def _on_overview(self, data):
        if not isinstance(data, dict) or data.get("error"):
            return
        self._update_overview(data)
        if self._settings.get("auto_launch_steamguard"):
            QTimer.singleShot(500, self._on_launch)

    def _update_overview(self, data):
        if not data:
            return
        rh = data.get("remaining_hours")
        if rh is None:
            rh = data.get("time_remaining_hours")
        if rh is None or (isinstance(rh, (int, float)) and rh >= 999990):
            time_text = "∞  Unlimited"
        elif isinstance(rh, (int, float)) and rh <= 0:
            time_text = "⚠  Expired"
        else:
            try:
                rh = float(rh)
                h = int(rh)
                m = int((rh - h) * 60)
                time_text = f"{h}h {m}m remaining"
            except (TypeError, ValueError):
                time_text = ""
        self._time_lbl.setText(time_text)

        # Also update tier
        tier = str(data.get("tier", "free")).upper()
        if self._tier_lbl is not None:
            self._tier_lbl.setText(tier)

        # Update activation date (Bug 4 fix — no more hardcoded placeholder)
        created = data.get("created_at", "")
        if created:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(str(created).replace("Z", ""))
                date_text = dt.strftime("%d.%m.%Y")
            except Exception:
                date_text = str(created)[:10]
            if self._sg_card_date is not None:
                self._sg_card_date.set_activated_date(date_text)

    # ── Rewards tab ───────────────────────────────────────────────────────
    def _build_rewards_tab(self):
        scroll, lay = self._scroll_page()

        top = QHBoxLayout()
        top.addWidget(self._header("Rewards"))
        top.addStretch(1)
        daily = QPushButton("Claim Daily")
        daily.setObjectName("CTA")
        daily.setCursor(Qt.PointingHandCursor)
        daily.setFixedHeight(36)
        daily.clicked.connect(self._claim_daily)
        top.addWidget(daily)
        lay.addLayout(top)

        self._rewards_inner = QWidget()
        self._rewards_inner.setStyleSheet("background:transparent;")
        self._rewards_lay = QGridLayout(self._rewards_inner)
        self._rewards_lay.setContentsMargins(0, 0, 0, 0)
        self._rewards_lay.setHorizontalSpacing(16)
        self._rewards_lay.setVerticalSpacing(16)
        self._rewards_lay.setAlignment(Qt.AlignTop)
        lay.addWidget(self._rewards_inner)
        lay.addStretch(1)

        self._rewards_msg = QLabel("Loading rewards...")
        self._rewards_msg.setObjectName("Muted")
        self._rewards_msg.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:9pt; background:transparent;")
        self._rewards_lay.addWidget(self._rewards_msg, 0, 0)
        return scroll

    def _load_rewards(self):
        if not self._token:
            self._render_rewards({"error": "Not authenticated"})
            return
        worker = GetJsonWorker(self._server + "/me/rewards", self._token)
        worker.done.connect(self._render_rewards)
        self._workers.append(worker)
        worker.start()

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_rewards(self, data):
        self._clear_layout(self._rewards_lay)
        if not isinstance(data, dict) or data.get("error"):
            msg = QLabel((data or {}).get("error", "Could not load rewards."))
            msg.setStyleSheet(f"color:{TEXT_DIM}; font-size:9pt; background:transparent;")
            self._rewards_lay.addWidget(msg, 0, 0)
            return
        rewards = data.get("rewards") or data.get("items") or []
        if not rewards:
            msg = QLabel("No rewards available right now.")
            msg.setStyleSheet(f"color:{TEXT_DIM}; font-size:9pt; background:transparent;")
            self._rewards_lay.addWidget(msg, 0, 0)
            return
        for i, r in enumerate(rewards):
            row, col = divmod(i, 2)
            self._rewards_lay.addWidget(self._reward_card(r), row, col)

    def _reward_card(self, r):
        card = QFrame()
        card.setObjectName("Card")
        card.setMinimumWidth(260)
        card.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        icon = IconWidget("star", ACCENT, 20, box=36)
        lay.addWidget(icon, alignment=Qt.AlignVCenter)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(str(r.get("name", "Reward")))
        name.setStyleSheet(f"color:{TEXT}; font-size:11pt; font-weight:700; background:transparent;")
        info.addWidget(name)
        lay.addLayout(info)
        lay.addStretch(1)

        hours = r.get("hours") or r.get("reward_hours")
        if hours:
            badge = QLabel(f"+{hours}h")
            badge.setStyleSheet(
                f"QLabel {{ color:{ACCENT}; border:1px solid {ACCENT};"
                " border-radius:0px; padding:2px 10px; font-size:9pt;"
                " font-weight:700; background:transparent; }}")
            lay.addWidget(badge, alignment=Qt.AlignTop)

        available = r.get("available", True)
        cooldown = r.get("cooldown_minutes") or r.get("cooldown")
        if available:
            btn = QPushButton("Claim")
            btn.setObjectName("CTA")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(30)
            rid = r.get("id") or r.get("name")
            btn.clicked.connect(lambda _=False, i=rid: self._claim_reward(i))
            lay.addWidget(btn)
        else:
            cd = QLabel(f"{cooldown}m" if cooldown else "Locked")
            cd.setStyleSheet(
                f"QLabel {{ color:{TEXT_DIM}; background:{FRAME_BG};"
                " border:1px solid %s; border-radius:0px; padding:4px 12px;"
                " font-size:8pt; font-weight:700; }}" % BORDER)
            lay.addWidget(cd)
        return card

    def _claim_reward(self, reward_id):
        worker = PostJsonWorker(self._server + "/rewards/claim", self._token,
                                {"reward_id": reward_id})
        worker.done.connect(lambda _r: self._load_rewards())
        self._workers.append(worker)
        worker.start()

    def _claim_daily(self):
        worker = PostJsonWorker(self._server + "/rewards/check-in", self._token, {})
        worker.done.connect(lambda _r: self._load_rewards())
        self._workers.append(worker)
        worker.start()

    # ── Referrals tab ─────────────────────────────────────────────────────
    def _build_referrals_tab(self):
        scroll, lay = self._scroll_page()
        lay.addWidget(self._header("Referrals"))

        # Referral link card
        link_card = QFrame()
        link_card.setObjectName("Card")
        link_card.setAttribute(Qt.WA_StyledBackground, True)
        lc = QVBoxLayout(link_card)
        lc.setContentsMargins(16, 14, 16, 14)
        lc.setSpacing(10)
        link_lbl = QLabel("Your referral link")
        link_lbl.setObjectName("Muted")
        link_lbl.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:9pt; font-weight:700; background:transparent;")
        lc.addWidget(link_lbl)

        link_row = QHBoxLayout()
        self._ref_link = QLineEdit("Loading...")
        self._ref_link.setReadOnly(True)
        self._ref_link.setFixedHeight(38)
        link_row.addWidget(self._ref_link, 1)
        link_row.addWidget(self._copy_button(lambda: self._copy(self._ref_link.text())))
        lc.addLayout(link_row)
        lay.addWidget(link_card)

        # Stat pills
        stats_row = QHBoxLayout()
        stats_row.setSpacing(16)
        self._stat_valid = self._stat_card("Valid", "0")
        self._stat_pending = self._stat_card("Pending", "0")
        self._stat_earned = self._stat_card("Earned (h)", "0")
        stats_row.addWidget(self._stat_valid[0])
        stats_row.addWidget(self._stat_pending[0])
        stats_row.addWidget(self._stat_earned[0])
        lay.addLayout(stats_row)

        # Invite message card
        msg_card = QFrame()
        msg_card.setObjectName("Card")
        msg_card.setAttribute(Qt.WA_StyledBackground, True)
        mc = QVBoxLayout(msg_card)
        mc.setContentsMargins(16, 14, 16, 14)
        mc.setSpacing(10)
        msg_lbl = QLabel("Invite message")
        msg_lbl.setObjectName("Muted")
        msg_lbl.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:9pt; font-weight:700; background:transparent;")
        mc.addWidget(msg_lbl)

        msg_row = QHBoxLayout()
        self._invite_msg = QLineEdit(
            "Join SteamGuard — the Steam Family Sharing unlocker! " + DISCORD_INVITE)
        self._invite_msg.setReadOnly(True)
        self._invite_msg.setFixedHeight(38)
        msg_row.addWidget(self._invite_msg, 1)
        msg_row.addWidget(self._copy_button(lambda: self._copy(self._invite_msg.text())))
        mc.addLayout(msg_row)
        lay.addWidget(msg_card)

        lay.addStretch(1)
        return scroll

    def _copy_button(self, slot):
        b = QPushButton("Copy")
        b.setObjectName("Ghost")
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(38)
        b.clicked.connect(slot)
        return b

    def _stat_card(self, label, value):
        card = QFrame()
        card.setObjectName("Card")
        card.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 16, 14, 16)
        v.setSpacing(4)
        val_lbl = QLabel(value)
        val_lbl.setAlignment(Qt.AlignCenter)
        val_lbl.setStyleSheet(f"color:{ACCENT}; font-size:18pt; font-weight:700; background:transparent;")
        name_lbl = QLabel(label)
        name_lbl.setObjectName("Muted")
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setStyleSheet(f"color:{TEXT_DIM}; font-size:9pt; background:transparent;")
        v.addWidget(val_lbl)
        v.addWidget(name_lbl)
        return card, val_lbl

    def _load_referrals(self):
        if not self._token:
            return
        if not self._referral_ensured:
            self._referral_ensured = True
            worker = PostJsonWorker(self._server + "/me/referral/create", self._token, {})
            worker.done.connect(lambda _r: self._fetch_referrals())
            self._workers.append(worker)
            worker.start()
        else:
            self._fetch_referrals()

    def _fetch_referrals(self):
        worker = GetJsonWorker(self._server + "/me/referrals", self._token)
        worker.done.connect(self._render_referrals)
        self._workers.append(worker)
        worker.start()

    def _render_referrals(self, data):
        if not isinstance(data, dict) or data.get("error"):
            return
        link = data.get("referral_link") or data.get("link")
        if link:
            self._ref_link.setText(link)
        code = data.get("referral_code") or data.get("code")
        if code and not link:
            self._ref_link.setText(f"{BRAND_SITE}/?ref={code}")
        self._stat_valid[1].setText(str(data.get("valid", 0)))
        self._stat_pending[1].setText(str(data.get("pending", 0)))
        earned = data.get("earned_hours") or data.get("earned") or 0
        self._stat_earned[1].setText(str(earned))

    def _copy(self, text):
        QApplication.clipboard().setText(text)

    # ── Settings tab ──────────────────────────────────────────────────────
    def _build_settings_tab(self):
        scroll, lay = self._scroll_page()
        lay.addWidget(self._header("Settings"))

        self._toggle_startup = self._make_toggle(
            "Launch on startup", self._settings.get("launch_on_startup", False))
        lay.addWidget(self._toggle_startup[0])

        self._toggle_autolaunch = self._make_toggle(
            "Auto-launch SteamGuard", self._settings.get("auto_launch_steamguard", False))
        lay.addWidget(self._toggle_autolaunch[0])

        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("CTA")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setFixedHeight(40)
        save_btn.clicked.connect(self._save_settings)
        lay.addWidget(save_btn)

        reset_btn = QPushButton("Reset saved credentials")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setFixedHeight(40)
        reset_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{RED};
                           border:1px solid {RED}; border-radius:0px;
                           padding:8px; font-size:9pt; font-weight:700; }}
            QPushButton:hover {{ background:{RED}; color:white; }}
        """)
        reset_btn.clicked.connect(self._reset_creds)
        lay.addWidget(reset_btn)

        self._settings_msg = QLabel("")
        self._settings_msg.setStyleSheet(f"color:{ACCENT}; font-size:9pt; background:transparent;")
        lay.addWidget(self._settings_msg)

        lay.addStretch(1)

        ver = QLabel(f"SteamGuard Loader {APP_VERSION}")
        ver.setObjectName("Muted")
        ver.setStyleSheet(f"color:{TEXT_DIM}; font-size:8pt; background:transparent;")
        lay.addWidget(ver)
        return scroll

    def _make_toggle(self, label, checked):
        row = QFrame()
        row.setObjectName("Card")
        row.setAttribute(Qt.WA_StyledBackground, True)
        h = QHBoxLayout(row)
        h.setContentsMargins(16, 12, 16, 12)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{TEXT}; font-size:10pt; background:transparent;")
        h.addWidget(lbl)
        h.addStretch(1)
        chk = QCheckBox()
        chk.setChecked(checked)
        chk.setCursor(Qt.PointingHandCursor)
        # ImGui square checkbox (styled globally by STYLESHEET)
        h.addWidget(chk)
        return row, chk

    def _save_settings(self):
        self._settings["launch_on_startup"] = self._toggle_startup[1].isChecked()
        self._settings["auto_launch_steamguard"] = self._toggle_autolaunch[1].isChecked()
        save_settings(self._settings)
        self._apply_startup(self._settings["launch_on_startup"])
        self._settings_msg.setText("Settings saved.")

    def _apply_startup(self, enable):
        """Add/remove a Run registry key for launch-on-startup (Windows only)."""
        try:
            import winreg
            run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
            exe = sys.executable if getattr(sys, "frozen", False) else \
                f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0,
                                winreg.KEY_SET_VALUE) as k:
                if enable:
                    winreg.SetValueEx(k, "SteamGuardLoader", 0, winreg.REG_SZ, exe)
                else:
                    try:
                        winreg.DeleteValue(k, "SteamGuardLoader")
                    except FileNotFoundError:
                        pass
        except Exception:
            pass

    def _reset_creds(self):
        clear_creds()
        self._settings_msg.setText("Saved credentials cleared.")

    def _on_logout(self):
        clear_creds()
        for proc in list(self._processes.values()):
            try:
                if proc.poll() is None:
                    proc.terminate()
            except Exception:
                pass
        self._processes.clear()
        self._proc_timer.stop()
        self.logout.emit()


# ══════════════════════════════════════════════════════════════════════════════
#  Loading screen — concentric arc spinner during app init
# ══════════════════════════════════════════════════════════════════════════════

class LoadingScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._spinner = Spinner(80, self)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._spinner, alignment=Qt.AlignCenter)

    def start(self):
        self._spinner.start()

    def stop(self):
        self._spinner.stop()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG))
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  Main window (native title bar + dark styling via pywinstyles)
# ══════════════════════════════════════════════════════════════════════════════

class LoaderWindow(QWidget):
    def __init__(self):
        super().__init__()
        # Native title bar (NOT frameless). Compact 1000x640 = more ImGui.
        self.setWindowTitle("Rivvak Community")
        self.setMinimumSize(1000, 640)
        self.resize(1000, 640)
        self.setStyleSheet(f"background:{BG};")

        try:
            ico = Path(__file__).parent / "icon.ico"
            if ico.exists():
                self.setWindowIcon(QIcon(str(ico)))
        except Exception:
            pass

        self._stack = QStackedWidget(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

        # Loading screen (index 0) shown briefly during init
        self._loading = LoadingScreen()
        self._stack.addWidget(self._loading)

        self._login = LoginScreen(self)
        self._login.login_success.connect(self._go_dashboard)
        self._stack.addWidget(self._login)

        self._dashboard = None
        self._stack.setCurrentWidget(self._loading)
        self._loading.start()
        self._center()

        # apply custom dark title-bar styling on Windows
        QTimer.singleShot(0, self._apply_titlebar_style)

    def _apply_titlebar_style(self):
        try:
            import pywinstyles
            pywinstyles.apply_style(self, "dark")
            pywinstyles.change_header_color(self, TITLE_BG)
            pywinstyles.change_title_color(self, "#FFFFFF")
        except Exception:
            pass

    def show_login(self):
        self._loading.stop()
        self._fade_to(self._login)

    def _center(self):
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move((screen.width() - self.width()) // 2,
                      (screen.height() - self.height()) // 2)
        except Exception:
            pass

    def _fade_to(self, widget):
        self._stack.setCurrentWidget(widget)
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.finished.connect(lambda: widget.setGraphicsEffect(None))
        anim.start()
        self._current_anim = anim   # keep reference

    def _go_dashboard(self, session):
        if self._dashboard is not None:
            self._stack.removeWidget(self._dashboard)
            self._dashboard.deleteLater()
        self._dashboard = Dashboard(self, session)
        self._dashboard.logout.connect(self._go_login)
        self._stack.addWidget(self._dashboard)
        self._fade_to(self._dashboard)

    def _go_login(self):
        self._fade_to(self._login)


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    # Anti-tamper checks (silent — never crash the loader if the module is absent)
    try:
        from auth.loader_guard import run_checks
        results = run_checks(silent=True)
        if results.get("debugger") or results.get("bad_processes"):
            try:
                ctypes = __import__("ctypes")
                ctypes.windll.user32.MessageBoxW(
                    0, "A prohibited analysis tool was detected.\n"
                       "Close it and relaunch SteamGuard Loader.",
                    "SteamGuard", 0x10)
            except Exception:
                pass
            sys.exit(1)
    except Exception:
        pass

    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass

    app = QApplication(sys.argv)
    try:
        pal = app.palette()
        pal.setColor(pal.PlaceholderText, QColor(TEXT_DIM))
        app.setPalette(pal)
    except Exception:
        pass
    app.setStyle("Fusion")  # Required for QPushButton to respect background-color
    app.setApplicationName("Rivvak Community")
    app.setStyleSheet(STYLESHEET)
    win = LoaderWindow()
    win.show()
    # Apply Windows dark backdrop for the theme
    try:
        hwnd = int(win.winId())
        _apply_acrylic(hwnd)
    except Exception:
        pass

    # Brief loading state, then reveal the login screen + attempt auto-login.
    def _reveal():
        win.show_login()
        QTimer.singleShot(300, win._login.try_auto_login)

    QTimer.singleShot(900, _reveal)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
