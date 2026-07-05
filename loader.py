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

BG          = "#0F0F0F"   # WindowBg — very dark near-black
BG_CHILD    = "#121212"   # ChildBg — cards/panels
BG_POPUP    = "#141414"   # PopupBg — dropdowns/tooltips
BORDER      = "#3E3E47"   # 1px borders everywhere
ACCENT      = "#4296FA"   # Electric blue — hover/active/checkmarks
ACCENT_ACT  = "#0F87FA"   # Pressed state — brighter blue
ACCENT_DIM  = "#24456D"   # Button idle — accent @ 40% over bg
FRAME_BG    = "#1D2F49"   # Input/frame background
HEADER      = "#1F3958"   # Active nav item background
TEXT        = "#FFFFFF"   # Primary text
TEXT_DIM    = "#808080"   # Disabled/secondary text
TITLE_BG    = "#0A0A0A"   # Sidebar/title bar

# Hover wash for nav items
NAV_HOVER   = "#16273D"

# ── Legacy-compatible aliases kept so preserved logic keeps working ────────────
ACCENT2      = ACCENT
RED          = "#F23F43"
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

# Default license/API server. The auth API is served under rivvak.app; override
# via the SG_SERVER_URL env var.
DEFAULT_SERVER_URL = os.environ.get("SG_SERVER_URL", "https://rivvak.app")

# ── Tool download (loader fetches SteamGuard.exe via rivvak.app/get-tool) ─────
STEAMGUARD_DOWNLOAD_URL = f"{DEFAULT_SERVER_URL}/get-tool?tool=steamguard"
TOOLS_DIR = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "SteamGuard" / "tools"


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
* { border-radius: 0px; outline: 0; font-family: 'Consolas', 'JetBrains Mono', monospace; font-size: 10pt; color: #FFFFFF; }
QMainWindow, QDialog, QWidget { background-color: #0F0F0F; color: #FFFFFF; }
QFrame { background-color: #0F0F0F; border: none; }
QFrame#Card { background-color: #121212; border: 1px solid #3E3E47; }
QLabel { background: transparent; color: #FFFFFF; }
QLabel#Muted { color: #808080; }
QLabel#Title { color: #FFFFFF; font-weight: 700; font-size: 14pt; }
QLabel#CardTitle { color: #FFFFFF; font-weight: 700; font-size: 11pt; }
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #1D2F49; color: #FFFFFF;
    border: 1px solid #3E3E47; border-radius: 0px;
    padding: 6px 10px; font-size: 10pt; selection-background-color: #4296FA;
}
QLineEdit:focus { border: 1px solid #4296FA; }
QLineEdit::placeholder { color: #808080; }
QPushButton {
    background-color: #24456D; color: #FFFFFF; border: none;
    border-radius: 0px; padding: 6px 16px; font-size: 10pt; font-weight: 600;
    min-height: 28px;
}
QPushButton:hover { background-color: #4296FA; color: #FFFFFF; }
QPushButton:pressed { background-color: #0F87FA; }
QPushButton:disabled { background-color: #1A1A1A; color: #808080; }
QPushButton#CTA {
    background-color: #4296FA; color: #FFFFFF; font-weight: 700;
    border: none; border-radius: 0px; padding: 8px 24px; min-height: 36px;
}
QPushButton#CTA:hover { background-color: #5BA3FF; }
QPushButton#CTA:pressed { background-color: #0F87FA; }
QPushButton#Ghost {
    background-color: #1C1C1C; color: #E6E6E6;
    border: 1px solid #2A2A2A; border-radius: 0px;
}
QPushButton#Ghost:hover { background-color: #24456D; border-color: #4296FA; }
QCheckBox { color: #FFFFFF; font-size: 10pt; }
QCheckBox::indicator { width: 14px; height: 14px; background: #1D2F49; border: 1px solid #3E3E47; border-radius: 0px; }
QCheckBox::indicator:checked { background: #4296FA; border-color: #4296FA; }
QScrollBar:vertical { background: #0F0F0F; width: 8px; border: none; }
QScrollBar::handle:vertical { background: #4F4F4F; border-radius: 0px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: #4296FA; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #141414; color: #FFFFFF; border: 1px solid #3E3E47; padding: 4px 8px; font-size: 9pt; }
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
        import urllib.request, ssl
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
        except Exception as e:
            self.error.emit(f"Download failed: {str(e)[:120]}")
            self.done.emit("")


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
    """Top brand slot: 72px square, RC hexagon shield inside (no radius)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(72, 56)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        _draw_icon(p, "logo", 36, 28, TEXT, 30)
        p.end()


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
            icon_col = "rgba(255,255,255,0.65)"
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
                 parent=None):
        super().__init__(parent)
        self._title = title
        self._glyph = glyph
        self._grad_top = QColor(grad_top)
        self._grad_bottom = QColor(grad_bottom)
        self._activated_date = activated_date
        self.setObjectName("Card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(330)

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
        name_row.addWidget(icon, alignment=Qt.AlignVCenter)
        name_lbl = QLabel(title)
        name_lbl.setStyleSheet(
            f"color:{TEXT}; font-size:12pt; font-weight:700; background:transparent;")
        name_row.addWidget(name_lbl)
        name_row.addStretch(1)
        bl.addLayout(name_row)

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
        grad.setColorAt(0, self._grad_top)
        grad.setColorAt(1, self._grad_bottom)
        p.fillRect(art_rect, QBrush(grad))

        # Large, subtle, clipped stylized tool name over the gradient (Bug 2 fix)
        p.save()
        p.setClipRect(art_rect)          # clip to art area
        font = QFont("Consolas", 1)
        font.setPixelSize(42)            # was 70
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        p.setOpacity(0.12)               # more subtle
        p.setPen(QColor(255, 255, 255))
        p.drawText(QRect(14, 14, self.width() - 28, img_h - 28),
                   Qt.AlignBottom | Qt.AlignLeft, self._title)
        p.restore()

        # Hairline under the art area
        p.setPen(QPen(QColor(BORDER), 1))
        p.drawLine(1, img_h, self.width() - 1, img_h)

        # 1px border (QSS #Card also styles this, drawn here for the painted overlay)
        p.setPen(QPen(QColor(BORDER), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
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
        c.setContentsMargins(40, 32, 40, 32)
        c.setSpacing(0)

        # Title
        title = QLabel("RIVVAK COMMUNITY")
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color:{TEXT}; font-size:14pt; font-weight:700; background:transparent;"
            " letter-spacing:1px;")
        c.addWidget(title)
        c.addSpacing(4)

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

        # Social buttons row (Discord | YouTube) — Ghost style
        social = QHBoxLayout()
        social.setSpacing(10)
        social.setContentsMargins(0, 0, 0, 0)
        discord_btn = QPushButton("Discord")
        discord_btn.setObjectName("Ghost")
        discord_btn.setCursor(Qt.PointingHandCursor)
        discord_btn.setFixedHeight(34)
        discord_btn.clicked.connect(lambda: webbrowser.open(DISCORD_INVITE))
        youtube_btn = QPushButton("YouTube")
        youtube_btn.setObjectName("Ghost")
        youtube_btn.setCursor(Qt.PointingHandCursor)
        youtube_btn.setFixedHeight(34)
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
            f"QPushButton {{ background:transparent; color:{ACCENT};"
            " border:none; min-height:0; padding:0; font-size:9pt; }}"
            " QPushButton:hover { text-decoration:underline; color:#5BA3FF; }")
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
        self._sg_process = None
        self._proc_timer = QTimer(self)
        self._proc_timer.timeout.connect(self._check_process)
        self._workers = []   # keep references alive
        self._referral_ensured = False
        self._tier_lbl = None
        self._sg_card_date = None
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
        self._tier_lbl = QLabel("")
        self._tier_lbl.setStyleSheet(
            f"color:{ACCENT}; font-size:10pt; font-weight:700; background:transparent;")
        head_row.addWidget(self._tier_lbl, alignment=Qt.AlignVCenter)
        lay.addLayout(head_row)

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
        )
        self._sg_card_date = self._sg_card
        grid.addWidget(self._sg_card, 0, 0)

        # Card 2 — Roblox Tool (dark blue/purple), Coming Soon
        self._roblox_card = ProductCard(
            "Roblox Tool", "circle",
            grad_top="#1a1a38", grad_bottom="#0d0d20",
            action_text="Coming Soon", action_enabled=False,
        )
        grid.addWidget(self._roblox_card, 0, 1)

        # Card 3 — Coming Soon (dark grey)
        self._soon_card = ProductCard(
            "Coming Soon", "lock",
            grad_top="#1a1a20", grad_bottom="#0d0d12",
            action_text="Coming Soon", action_enabled=False,
        )
        grid.addWidget(self._soon_card, 0, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)
        lay.addStretch(1)

        # status/time label (repurposes the old time label for messages)
        self._time_lbl = QLabel("")
        self._time_lbl.setObjectName("Muted")
        self._time_lbl.setStyleSheet(
            f"color:{TEXT_DIM}; font-size:9pt; background:transparent;")
        lay.addWidget(self._time_lbl)
        return scroll

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

    def _start_download(self):
        """Download SteamGuard.exe from the server with a progress indicator."""
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
        self._sg_card.reset_action("Launch", ACCENT)
        if path:
            self._launch_exe(path)
        else:
            self._time_lbl.setText("Download failed — check connection")

    def _on_dl_error(self, msg: str):
        self._sg_card.reset_action("Launch", ACCENT)
        self._time_lbl.setText(f"{msg[:60]}")

    def _launch_exe(self, exe):
        """Launch SteamGuard.exe and start monitoring the process."""
        try:
            self._sg_process = subprocess.Popen([str(exe)])
        except Exception as e:
            self._time_lbl.setText("Launch failed")
            return
        self._sg_card.set_action_running()
        self._proc_timer.start(1000)

    def _check_process(self):
        if self._sg_process is None:
            self._proc_timer.stop()
            return
        if self._sg_process.poll() is not None:
            self._proc_timer.stop()
            self._sg_process = None
            self._sg_card.reset_action("Launch", ACCENT)

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
        if self._sg_process:
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
