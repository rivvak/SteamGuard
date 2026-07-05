"""
SteamGuard Loader — Rivvak Community edition
============================================
A premium PyQt5 front-end for the SteamGuard client, styled as a **pixel-perfect
1:1 re-creation of the Figma design** ("Cheat Loader imgui (Community)").

The window uses a native title bar with custom dark styling (pywinstyles on
Windows). Every icon and decorative shape is drawn with QPainter — there is
ZERO base64 image loading, ZERO QPixmap.loadFromData, and ZERO PIL usage for
display. Backgrounds for the product cards are painted QPainter linear
gradients (no bitmap art).

Screens:
  * Login  — glass modal card over a darkened sidebar (initial screen).
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

# ══════════════════════════════════════════════════════════════════════════════
#  EXACT Figma colour palette (source-of-truth hex values)
# ══════════════════════════════════════════════════════════════════════════════

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
                         GradientColor=0x99141820,  # #141820 @ 60% = dark tinted acrylic
                         AnimationId=0)
        data = _WCA_DATA(Attribute=19,  # WCA_ACCENT_POLICY
                         pData=ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                         cbData=ctypes.sizeof(accent))
        ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
    except Exception:
        pass  # Not Windows or API unavailable



BG              = "#0F1014"   # app/window background ("Splash Background")
SIDEBAR_FILL    = "#14151C"   # sidebar panel (blur simulated with solid)
SIDEBAR_DIVIDER = "#1A1C25"   # 1px vertical divider on the sidebar's right edge
HILITE_PILL     = QColor(35, 38, 50, int(0.29 * 255))   # #232632 @ 29%
SLOT_REST       = "#1A1C25"   # icon-slot resting fill, radius 21
SLOT_ACTIVE     = "#1C1F28"   # icon-slot active fill
NAV_OUTER       = "#161920"   # "Game page" nav slot outer
NAV_INNER       = "#1C1F28"   # "Game page" nav slot inner
LOGO_SLOT_FILL  = QColor(26, 28, 37, int(0.6 * 255))    # #1A1C25 @ 60%

CARD_SURFACE    = "#131418"   # card/panel dark surface
MODAL_CARD      = "#161514"   # login modal "Rectangle 30"
INPUT_MODAL     = QColor(46, 46, 47, int(0.37 * 255))   # #2E2E2F @ 37%
INPUT_DASH      = "#20222A"   # dark translucent action-button fill

ACCENT_BLUE     = "#C7EBFF"   # PLAY / light-blue CTA
ACCENT_GOLD     = "#ECD997"   # Activate / LOGIN gold CTA
SPINNER_GREEN   = "#35B18E"   # loading spinner outer arc
SPINNER_BLUE    = "#1681FF"   # loading spinner inner arc

TEXT            = "#FFFFFF"
TEXT_MUTED      = "rgba(255,255,255,0.6)"
PLACEHOLDER     = "#5A5A5A"
ICON_GREY       = "#A5A5A5"

# Discord / YouTube brand colours
DISCORD_BLURPLE = "#5865F2"
YOUTUBE_RED     = "#FF0000"

# Legacy-compatible aliases kept so preserved logic keeps working.
ACCENT   = SPINNER_GREEN
ACCENT2  = ACCENT_BLUE
RED      = "#F23F43"
YELLOW   = ACCENT_GOLD
MUTED    = "#6E7681"
MUTED2   = "#8B949E"
SURFACE  = CARD_SURFACE
BORDER   = "#30363D"
ACCENT_HOVER = "#2FBF6B"
ACCENT_DIM   = "#1B8047"

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
#  QSS stylesheet (exact spec)
# ══════════════════════════════════════════════════════════════════════════════
STYLESHEET = """
QWidget { background: transparent; color: #FFFFFF; font-family: 'Segoe UI', Inter, sans-serif; }
QScrollBar:vertical { background: #131418; width: 6px; border-radius: 3px; }
QScrollBar::handle:vertical { background: #232632; border-radius: 3px; min-height: 20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QLineEdit {
    background: rgba(46,46,47,0.37);
    border: none;
    border-radius: 10px;
    color: white;
    font-size: 16px;
    padding: 0 16px;
}
QLineEdit::placeholder { color: #5A5A5A; }
QPushButton#login-btn {
    background: #ECD997;
    border-radius: 10px;
    color: #000000;
    font-size: 14px;
    font-weight: 700;
}
QPushButton#login-btn:hover { background: #f0e0a0; }
QPushButton#discord-btn { background: #5865F2; border-radius: 29px; color: white; font-size: 12px; font-weight: 700; }
QPushButton#youtube-btn { background: #FF0000; border-radius: 29px; color: white; font-size: 12px; font-weight: 700; }
QCheckBox { color: rgba(255,255,255,0.65); font-size: 12px; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid rgba(255,255,255,0.3); border-radius: 4px; background: rgba(46,46,47,0.5); }
QCheckBox::indicator:checked { background: #ECD997; border-color: #ECD997; }
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

def _draw_rc_shield(p, cx, cy, r, color):
    """RC hexagon shield (the MY.GAMES-equivalent brand logo). Solid fill."""
    path = QPainterPath()
    top = cy - r
    path.moveTo(cx, top)
    path.lineTo(cx + r * 0.92, cy - r * 0.38)
    path.lineTo(cx + r * 0.92, cy + r * 0.30)
    path.quadTo(cx + r * 0.92, cy + r * 0.78, cx, cy + r * 1.02)
    path.quadTo(cx - r * 0.92, cy + r * 0.78, cx - r * 0.92, cy + r * 0.30)
    path.lineTo(cx - r * 0.92, cy - r * 0.38)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)
    # inner notch (gives the shield a stylized "M/V" cut like the Figma logo)
    notch = QPainterPath()
    notch.moveTo(cx - r * 0.42, cy - r * 0.30)
    notch.lineTo(cx, cy + r * 0.16)
    notch.lineTo(cx + r * 0.42, cy - r * 0.30)
    notch.lineTo(cx + r * 0.24, cy - r * 0.30)
    notch.lineTo(cx, cy - r * 0.06)
    notch.lineTo(cx - r * 0.24, cy - r * 0.30)
    notch.closeSubpath()
    p.setBrush(QColor(BG))
    p.drawPath(notch)


def _draw_gamepad(p, cx, cy, s, color):
    """Simple gamepad line-icon: rounded body + two circular buttons + stick dots."""
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    body = QRectF(cx - s * 0.6, cy - s * 0.32, s * 1.2, s * 0.72)
    p.drawRoundedRect(body, s * 0.28, s * 0.28)
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

    if kind == "shield":
        _draw_rc_shield(p, cx, cy, r, color)

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
        p.drawRoundedRect(QRectF(cx - r, cy - r * 0.42, r * 1.05, r * 0.84),
                          r * 0.42, r * 0.42)
        p.drawRoundedRect(QRectF(cx - 0.05 * r, cy - r * 0.42, r * 1.05, r * 0.84),
                          r * 0.42, r * 0.42)
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
        # Door + arrow = logout icon (cleaner than "+" which confused users)
        pen = QPen(QColor(255, 255, 255, 200), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        cx = int(rect.center().x())
        cy = int(rect.center().y())
        # Door outline (3 sides open on right)
        p.drawLine(cx - 6, cy - 7, cx - 6, cy + 7)   # left edge
        p.drawLine(cx - 6, cy - 7, cx + 1, cy - 7)   # top edge
        p.drawLine(cx - 6, cy + 7, cx + 1, cy + 7)   # bottom edge
        # Arrow pointing right (exit)
        p.drawLine(cx - 1, cy, cx + 7, cy)            # shaft
        p.drawLine(cx + 4, cy - 3, cx + 7, cy)        # arrow top
        p.drawLine(cx + 4, cy + 3, cx + 7, cy)        # arrow bottom

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
        p.drawRoundedRect(QRectF(cx - r * 0.65, cy - r * 0.15, r * 1.3, r),
                          r * 0.18, r * 0.18)
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
    """Two concentric rotating arcs — outer green (#35B18E), inner blue (#1681FF).

    Stroke width 12px in the Figma spec; scaled with the widget. Animated ~60fps.
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
        # Outer green arc
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
    """Circular avatar with the first letter of the username (gold accent)."""

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
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(SLOT_ACTIVE))
        p.drawEllipse(0, 0, self._size, self._size)
        p.setPen(QColor(ACCENT_GOLD))
        f = QFont("Segoe UI", int(self._size * 0.42))
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self._letter)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  Sidebar (Gamebar) — pixel-accurate re-creation
# ══════════════════════════════════════════════════════════════════════════════

class LogoSlot(QWidget):
    """Top brand slot: 71x70 rounded square (radius 30), RC shield inside."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(71, 70)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(LOGO_SLOT_FILL)
        p.drawRoundedRect(QRectF(0, 0, 71, 70), 30, 30)
        _draw_rc_shield(p, 35.5, 35, 20, TEXT)
        p.end()


class NavGamepadSlot(QWidget):
    """The 'Game page' nav slot: 71x70 outer (#161920 r30) + 49x48 inner
    (#1C1F28 r21) with a gamepad line-icon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(71, 70)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(NAV_OUTER))
        p.drawRoundedRect(QRectF(0, 0, 71, 70), 30, 30)
        p.setBrush(QColor(NAV_INNER))
        p.drawRoundedRect(QRectF((71 - 49) / 2, (70 - 48) / 2, 49, 48), 21, 21)
        _draw_gamepad(p, 35.5, 35, 20, ICON_GREY)
        p.end()


class SideIconButton(QPushButton):
    """A 53x51 rounded-square (radius 21) nav slot with a QPainter glyph.

    Resting fill #1A1C25 + grey icon; active fill #1C1F28 + white icon.
    """

    def __init__(self, kind, tooltip="", parent=None):
        super().__init__(parent)
        self._kind = kind
        self._active = False
        self._hover = False
        self.setCheckable(kind not in ("plus", "logout"))
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(53, 51)
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
        rect = QRectF(0, 0, 53, 51)
        p.setPen(Qt.NoPen)
        if self._active:
            p.setBrush(QColor(SLOT_ACTIVE))
            icon_col = TEXT
        elif self._hover:
            p.setBrush(QColor(SLOT_ACTIVE))
            icon_col = TEXT_MUTED_HEX
        else:
            p.setBrush(QColor(SLOT_REST))
            icon_col = ICON_GREY
        p.drawRoundedRect(rect, 21, 21)
        if self._kind in ("plus", "logout"):
            icon_col = "rgba(255,255,255,0.6)"
        _draw_icon(p, self._kind, 26.5, 25.5, icon_col, 22)
        p.end()


TEXT_MUTED_HEX = "#D0D2D8"


class Sidebar(QFrame):
    """80px-wide sidebar (Figma 101px scaled). Left-rounded panel, divider,
    active highlight pill, logo slot, gamepad nav slot, 4 nav buttons + logout."""

    def __init__(self, on_nav, on_logout, parent=None):
        super().__init__(parent)
        self.setFixedWidth(80)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;")
        self._on_nav = on_nav
        self._active_index = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 16, 0, 16)
        lay.setSpacing(0)
        lay.setAlignment(Qt.AlignHCenter)

        # Top brand logo slot
        lay.addWidget(LogoSlot(), alignment=Qt.AlignHCenter)
        lay.addSpacing(24)

        # Gamepad nav slot (decorative "Game page")
        lay.addWidget(NavGamepadSlot(), alignment=Qt.AlignHCenter)
        lay.addSpacing(20)

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
            lay.addSpacing(12)
            self._nav.append(btn)
        self._nav[0].setChecked(True)

        lay.addStretch(1)

        # Bottom logout button (arrow-out icon, not "+" which is confusing)
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

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        # Panel: left corners rounded 36px (blur simulated with solid fill)
        path = QPainterPath()
        path.moveTo(36, 0)
        path.lineTo(w, 0)
        path.lineTo(w, h)
        path.lineTo(36, h)
        path.quadTo(0, h, 0, h - 36)
        path.lineTo(0, 36)
        path.quadTo(0, 0, 36, 0)
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(SIDEBAR_FILL))
        p.drawPath(path)

        # Active highlight pill (71px wide, tall) behind the active nav group.
        if 0 <= self._active_index < len(self._nav):
            btn = self._nav[self._active_index]
            geo = btn.geometry()
            pill_w = 71
            px = (w - pill_w) / 2
            py = geo.y() - 6
            ph = geo.height() + 12
            p.setBrush(HILITE_PILL)
            p.drawRoundedRect(QRectF(px, py, pill_w, ph), 30, 30)

        # Vertical divider on the right edge
        p.setPen(QPen(QColor(SIDEBAR_DIVIDER), 1))
        p.drawLine(w - 1, 0, w - 1, h)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  Product cards — painted gradient art, no images
# ══════════════════════════════════════════════════════════════════════════════

class ProductCard(QFrame):
    """A Figma "Popüler ürünler" product card:

    - Top image area (~55% height) painted as a QPainter linear gradient with a
      large dimly-visible stylized tool name.
    - Bottom translucent section: logo glyph + tool name, optional activation
      date row, and a full-width action button.
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
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;")
        self.setFixedHeight(370)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # spacer for the painted image header (~55%)
        lay.addStretch(1)

        # bottom info block
        bottom = QWidget()
        bottom.setStyleSheet("background:transparent;")
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(18, 12, 18, 16)
        bl.setSpacing(10)

        # logo + name row
        name_row = QHBoxLayout()
        name_row.setSpacing(10)
        name_row.setContentsMargins(0, 0, 0, 0)
        icon = IconWidget(glyph, TEXT, 22, box=26)
        name_row.addWidget(icon, alignment=Qt.AlignVCenter)
        name_lbl = QLabel(title)
        name_lbl.setStyleSheet(
            f"color:{TEXT}; font-size:16px; font-weight:600; background:transparent;")
        name_row.addWidget(name_lbl)
        name_row.addStretch(1)
        bl.addLayout(name_row)

        # activation date row
        if activated_date:
            date_row = QHBoxLayout()
            date_row.setSpacing(8)
            date_row.setContentsMargins(0, 0, 0, 0)
            act_lbl = QLabel("Activated:")
            act_lbl.setStyleSheet(
                "color:rgba(255,255,255,0.5); font-size:12px; background:transparent;")
            date_row.addWidget(act_lbl)
            date_row.addStretch(1)
            clock = IconWidget("clock", "rgba(255,255,255,0.6)", 14, box=18)
            date_row.addWidget(clock, alignment=Qt.AlignVCenter)
            dval = QLabel(activated_date)
            dval.setStyleSheet(
                "color:rgba(255,255,255,0.85); font-size:13px; font-weight:600; background:transparent;")
            date_row.addWidget(dval)
            bl.addLayout(date_row)

        # action button (full width minus padding, 52px, radius 14)
        self.action_btn = QPushButton(action_text)
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.setFixedHeight(52)
        self._enabled = action_enabled
        self._style_action(action_enabled)
        if action_enabled and on_action is not None:
            self.action_btn.clicked.connect(on_action)
        if not action_enabled:
            self.action_btn.setDisabled(True)
        bl.addWidget(self.action_btn)

        lay.addWidget(bottom)

    def _style_action(self, enabled):
        col = TEXT if enabled else "rgba(255,255,255,0.5)"
        self.action_btn.setStyleSheet(f"""
            QPushButton {{ background:{INPUT_DASH}; color:{col}; border:none;
                           border-radius:14px; font-weight:600; font-size:14px; }}
            QPushButton:hover {{ background:#262933; }}
            QPushButton:disabled {{ background:{INPUT_DASH}; color:rgba(255,255,255,0.45); }}
        """)

    # helpers used by the preserved download/launch flow
    def set_action_label(self, text):
        self.action_btn.setText(text)

    def set_action_running(self):
        self.action_btn.setText("Running ●")
        self.action_btn.setDisabled(True)

    def reset_action(self, text, _color=None):
        self.action_btn.setText(text)
        self.action_btn.setDisabled(False)
        self._style_action(True)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        radius = 22
        rect = QRectF(0, 0, self.width(), self.height())

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.setClipPath(path)

        # Card base
        p.fillRect(self.rect(), QColor(CARD_SURFACE))

        # Top image area gradient (~55%)
        img_h = self.height() * 0.55
        grad = QLinearGradient(0, 0, self.width(), img_h)
        grad.setColorAt(0, self._grad_top)
        grad.setColorAt(1, self._grad_bottom)
        p.fillRect(QRectF(0, 0, self.width(), img_h), QBrush(grad))

        # Large dimly-visible stylized tool name over the gradient
        p.save()
        f = QFont("Segoe UI", 40)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 26))
        p.drawText(QRectF(18, 18, self.width() - 36, img_h - 36),
                   Qt.AlignBottom | Qt.AlignLeft, self._title)
        p.restore()

        # Bottom translucent overlay (rgba(0,0,0,0.16))
        p.fillRect(QRectF(0, img_h, self.width(), self.height() - img_h),
                   QColor(0, 0, 0, int(0.16 * 255)))
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  Login modal card (over the dimmed sidebar background)
# ══════════════════════════════════════════════════════════════════════════════

class GlassCard(QFrame):
    """A QFrame that paints a glassmorphism background: dark translucent fill +
    thin white border + top shimmer. Works without actual backdrop blur on Windows."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)  # we handle painting ourselves

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        radius = 15.0

        # Base dark fill — #161514 at 92% opacity
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(22, 21, 20, 235)))
        p.drawRoundedRect(r, radius, radius)

        # Glass border — 1px white at 10% opacity
        p.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

        # Top inner shimmer (frosted glass highlight)
        shimmer = QLinearGradient(0, 0, 0, radius * 3)
        shimmer.setColorAt(0.0, QColor(255, 255, 255, 22))
        shimmer.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(shimmer))
        p.drawRoundedRect(r, radius, radius)

        p.end()


class LoginScreen(QWidget):
    login_success = pyqtSignal(dict)

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._win = window
        self._worker = None
        self._settings = load_settings()
        self._build()

    def _build(self):
        # Sidebar behind (darkened), center card on top.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        card = self._build_card()
        # overlay the card centered — we position it in resizeEvent
        self._card = card
        card.setParent(self)

        # A faint sidebar decoration behind the dim overlay
        self._sidebar_ghost = Sidebar(lambda i: None, lambda: None, self)
        self._sidebar_ghost.setEnabled(False)
        self._sidebar_ghost.hide()  # completely hidden — shown dimly in paintEvent only

    def _build_card(self):
        # Figma "Rectangle 30" is 566x275, but that height only fits the title +
        # inputs + primary CTA. We keep the 566px width and 15px radius and let the
        # card grow vertically to hold the social row + footer without overlap.
        card = GlassCard()
        card.setFixedWidth(566)

        c = QVBoxLayout(card)
        c.setContentsMargins(53, 26, 53, 26)
        c.setSpacing(0)

        # Title
        title = QLabel("Rivvak Community")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color:{TEXT}; font-size:24px; font-weight:600; background:transparent;")
        c.addWidget(title)
        c.addSpacing(4)

        # Subtitle
        sub = QLabel("Login to access your tools")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(
            "color:rgba(255,255,255,0.6); font-size:13px; background:transparent;")
        c.addWidget(sub)
        c.addSpacing(16)

        # Input 1 — Discord ID
        self._discord = QLineEdit()
        self._discord.setPlaceholderText("Discord ID")
        self._discord.setFixedSize(460, 59)
        self._discord.setStyleSheet(self._input_css())
        c.addWidget(self._discord, alignment=Qt.AlignHCenter)
        c.addSpacing(10)

        # Input 2 — License Key (masked)
        self._key = QLineEdit()
        self._key.setPlaceholderText("License Key")
        self._key.setEchoMode(QLineEdit.Password)
        self._key.setFixedSize(460, 59)
        self._key.setStyleSheet(self._input_css())
        c.addWidget(self._key, alignment=Qt.AlignHCenter)
        c.addSpacing(8)

        # Remember me + error row
        self._remember = QCheckBox("Remember me")
        self._remember.setCursor(Qt.PointingHandCursor)
        c.addWidget(self._remember, alignment=Qt.AlignLeft)

        self._error = QLabel("")
        self._error.setAlignment(Qt.AlignCenter)
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color:{RED}; font-size:11px; background:transparent;")
        self._error.hide()
        c.addWidget(self._error)
        c.addSpacing(6)

        # LOGIN button — gold background, inline stylesheet (QGraphicsEffect kills QSS bg)
        self._login_btn = QPushButton("LOGIN")
        self._login_btn.setCursor(Qt.PointingHandCursor)
        self._login_btn.setFixedSize(460, 57)
        self._login_btn.clicked.connect(self._on_login)
        self._login_btn.setStyleSheet("""
            QPushButton {
                background: #ECD997;
                color: #000000;
                border: none;
                border-radius: 10px;
                font-size: 14px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QPushButton:hover { background: #f5e4aa; }
            QPushButton:pressed { background: #d4c27a; }
            QPushButton:disabled { background: #5a5530; color: #888; }
        """)
        c.addWidget(self._login_btn, alignment=Qt.AlignHCenter)
        c.addSpacing(14)

        # Social buttons row (Discord | YouTube) pill style
        social = QHBoxLayout()
        social.setSpacing(12)
        social.setContentsMargins(0, 0, 0, 0)
        discord_btn = QPushButton("Discord")
        discord_btn.setObjectName("discord-btn")
        discord_btn.setCursor(Qt.PointingHandCursor)
        discord_btn.setFixedHeight(38)
        discord_btn.clicked.connect(lambda: webbrowser.open(DISCORD_INVITE))
        youtube_btn = QPushButton("YouTube")
        youtube_btn.setObjectName("youtube-btn")
        youtube_btn.setCursor(Qt.PointingHandCursor)
        youtube_btn.setFixedHeight(38)
        youtube_btn.clicked.connect(lambda: webbrowser.open(BRAND_SITE))
        social.addStretch(1)
        social.addWidget(discord_btn)
        social.addWidget(youtube_btn)
        social.addStretch(1)
        c.addLayout(social)
        c.addSpacing(8)

        # Footer links
        footer = QHBoxLayout()
        footer.setSpacing(18)
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

    def _input_css(self):
        return ("QLineEdit { background:rgba(46,46,47,0.37); border:none;"
                " border-radius:10px; color:white; font-size:19px; padding:0 18px; }")

    def _link(self, text, slot):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFlat(True)
        b.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{ACCENT_BLUE};"
            " border:none; font-size:12px; }}"
            " QPushButton:hover { text-decoration:underline; }")
        b.clicked.connect(slot)
        return b

    def resizeEvent(self, e):
        # dim overlay + center card + glow
        self._sidebar_ghost.setFixedHeight(self.height())
        self._sidebar_ghost.move(0, 0)
        cw, ch = self._card.width(), self._card.height()
        self._card.move((self.width() - cw) // 2, (self.height() - ch) // 2)
        self._card.raise_()
        super().resizeEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # base background
        p.fillRect(self.rect(), QColor(BG))
        # dim overlay rgba(18,18,18,0.83)
        p.fillRect(self.rect(), QColor(18, 18, 18, int(0.83 * 255)))
        # gold glow rect behind the card (590x267, radius 28, #ECD997 @15%)
        gw, gh = 590, 267
        gx = (self.width() - gw) / 2
        gy = (self.height() - gh) / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(236, 217, 151, int(0.15 * 255)))
        p.drawRoundedRect(QRectF(gx, gy, gw, gh), 28, 28)
        p.end()

    def try_auto_login(self):
        creds = load_creds()
        if creds.get("discord_user_id") and creds.get("key"):
            self._on_login(auto=True)

    def _set_busy(self, busy: bool):
        self._login_btn.setDisabled(busy)
        self._login_btn.setText("SIGNING IN…" if busy else "LOGIN")
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
        self._build()
        self._load_overview()

    def _build(self):
        main = QHBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        self._sidebar = Sidebar(self._switch_tab, self._on_logout)
        main.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background:transparent;")
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
        scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        scroll.viewport().setStyleSheet("background:transparent;")
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(65, 50, 40, 40)
        lay.setSpacing(24)
        scroll.setWidget(inner)
        return scroll, lay

    def _header(self, text):
        h = QLabel(text)
        h.setStyleSheet(
            f"color:{TEXT}; font-size:28px; font-weight:600; background:transparent;")
        return h

    # ── Products tab ("Popular products") ─────────────────────────────────
    def _build_products_tab(self):
        scroll, lay = self._scroll_page()

        lay.addWidget(self._header("Popular products"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(40)
        grid.setVerticalSpacing(40)

        # Card 1 — SteamGuard (dark green), activated + Launch
        self._sg_card = ProductCard(
            "SteamGuard", "shield",
            grad_top="#1a3a28", grad_bottom="#0d1a14",
            action_text="Launch", action_enabled=True,
            activated_date="20.10.2022",
            on_action=self._on_launch,
        )
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

        # subtle status label (repurposes the old time label for messages)
        self._time_lbl = QLabel("")
        self._time_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.6); font-size:12px; background:transparent;")
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
            self._time_lbl.setText("⚠ Download failed — check connection")

    def _on_dl_error(self, msg: str):
        self._sg_card.reset_action("Launch", ACCENT)
        self._time_lbl.setText(f"⚠ {msg[:60]}")

    def _launch_exe(self, exe):
        """Launch SteamGuard.exe and start monitoring the process."""
        try:
            self._sg_process = subprocess.Popen([str(exe)])
        except Exception as e:
            self._time_lbl.setText("⚠ Launch failed")
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
        remaining = data.get("remaining_hours")
        if remaining is None:
            remaining = data.get("time_remaining_hours")
        if remaining is not None:
            try:
                total_min = int(float(remaining) * 60)
                h, m = divmod(total_min, 60)
                self._time_lbl.setText(f"⏰ {h}h {m}m remaining")
            except Exception:
                pass
        if self._settings.get("auto_launch_steamguard"):
            QTimer.singleShot(500, self._on_launch)

    # ── Rewards tab ───────────────────────────────────────────────────────
    def _build_rewards_tab(self):
        scroll, lay = self._scroll_page()

        top = QHBoxLayout()
        top.addWidget(self._header("Rewards"))
        top.addStretch(1)
        daily = QPushButton("Claim Daily")
        daily.setCursor(Qt.PointingHandCursor)
        daily.setFixedHeight(38)
        daily.setStyleSheet(f"""
            QPushButton {{ background:{ACCENT_GOLD}; color:#000000; border:none;
                           border-radius:19px; padding:0 20px; font-weight:700;
                           font-size:12px; }}
            QPushButton:hover {{ background:#f0e0a0; }}
        """)
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

        self._rewards_msg = QLabel("Loading rewards…")
        self._rewards_msg.setStyleSheet(
            "color:rgba(255,255,255,0.6); font-size:12px; background:transparent;")
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
            msg.setStyleSheet("color:rgba(255,255,255,0.6); font-size:12px; background:transparent;")
            self._rewards_lay.addWidget(msg, 0, 0)
            return
        rewards = data.get("rewards") or data.get("items") or []
        if not rewards:
            msg = QLabel("No rewards available right now.")
            msg.setStyleSheet("color:rgba(255,255,255,0.6); font-size:12px; background:transparent;")
            self._rewards_lay.addWidget(msg, 0, 0)
            return
        for i, r in enumerate(rewards):
            row, col = divmod(i, 2)
            self._rewards_lay.addWidget(self._reward_card(r), row, col)

    def _reward_card(self, r):
        card = QFrame()
        card.setMinimumWidth(260)
        card.setStyleSheet(f"""
            QFrame {{ background:{CARD_SURFACE}; border:none; border-radius:17px; }}
        """)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        icon = IconWidget("star", ACCENT_GOLD, 22, box=40)
        lay.addWidget(icon, alignment=Qt.AlignVCenter)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(str(r.get("name", "Reward")))
        name.setStyleSheet(f"color:{TEXT}; font-size:14px; font-weight:700; background:transparent;")
        info.addWidget(name)
        lay.addLayout(info)
        lay.addStretch(1)

        hours = r.get("hours") or r.get("reward_hours")
        if hours:
            badge = QLabel(f"+{hours}h")
            badge.setStyleSheet(
                f"QLabel {{ color:{ACCENT_GOLD}; border:1px solid {ACCENT_GOLD};"
                " border-radius:11px; padding:2px 10px; font-size:11px;"
                " font-weight:700; background:transparent; }}")
            lay.addWidget(badge, alignment=Qt.AlignTop)

        available = r.get("available", True)
        cooldown = r.get("cooldown_minutes") or r.get("cooldown")
        if available:
            btn = QPushButton("Claim")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.setStyleSheet(f"""
                QPushButton {{ background:{ACCENT_GOLD}; color:#000000; border:none;
                               border-radius:14px; padding:0 16px; font-weight:700;
                               font-size:11px; }}
                QPushButton:hover {{ background:#f0e0a0; }}
            """)
            rid = r.get("id") or r.get("name")
            btn.clicked.connect(lambda _=False, i=rid: self._claim_reward(i))
            lay.addWidget(btn)
        else:
            cd = QLabel(f"{cooldown}m" if cooldown else "Locked")
            cd.setStyleSheet(
                "QLabel { color:rgba(255,255,255,0.6); background:rgba(255,255,255,0.06);"
                " border-radius:12px; padding:4px 12px; font-size:10px; font-weight:700; }")
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
        link_card.setStyleSheet(f"QFrame {{ background:{CARD_SURFACE}; border-radius:17px; }}")
        lc = QVBoxLayout(link_card)
        lc.setContentsMargins(20, 18, 20, 18)
        lc.setSpacing(10)
        link_lbl = QLabel("Your referral link")
        link_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.6); font-size:12px; font-weight:700; background:transparent;")
        lc.addWidget(link_lbl)

        link_row = QHBoxLayout()
        self._ref_link = QLineEdit("Loading…")
        self._ref_link.setReadOnly(True)
        self._ref_link.setFixedHeight(44)
        self._ref_link.setStyleSheet(
            f"QLineEdit {{ background:rgba(46,46,47,0.37); color:{ACCENT_BLUE};"
            " border:none; border-radius:10px; padding:0 14px; font-size:13px; }}")
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
        msg_card.setStyleSheet(f"QFrame {{ background:{CARD_SURFACE}; border-radius:17px; }}")
        mc = QVBoxLayout(msg_card)
        mc.setContentsMargins(20, 18, 20, 18)
        mc.setSpacing(10)
        msg_lbl = QLabel("Invite message")
        msg_lbl.setStyleSheet(
            "color:rgba(255,255,255,0.6); font-size:12px; font-weight:700; background:transparent;")
        mc.addWidget(msg_lbl)

        msg_row = QHBoxLayout()
        self._invite_msg = QLineEdit(
            "Join SteamGuard — the Steam Family Sharing unlocker! " + DISCORD_INVITE)
        self._invite_msg.setReadOnly(True)
        self._invite_msg.setFixedHeight(44)
        self._invite_msg.setStyleSheet(
            f"QLineEdit {{ background:rgba(46,46,47,0.37); color:{TEXT};"
            " border:none; border-radius:10px; padding:0 14px; font-size:12px; }}")
        msg_row.addWidget(self._invite_msg, 1)
        msg_row.addWidget(self._copy_button(lambda: self._copy(self._invite_msg.text())))
        mc.addLayout(msg_row)
        lay.addWidget(msg_card)

        lay.addStretch(1)
        return scroll

    def _copy_button(self, slot):
        b = QPushButton("Copy")
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(44)
        b.setStyleSheet(
            f"QPushButton {{ background:{INPUT_DASH}; color:{TEXT}; border:none;"
            " border-radius:10px; padding:0 18px; font-size:12px; }}"
            " QPushButton:hover { background:#262933; }")
        b.clicked.connect(slot)
        return b

    def _stat_card(self, label, value):
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background:{CARD_SURFACE}; border-radius:17px; }}")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 18, 14, 18)
        v.setSpacing(4)
        val_lbl = QLabel(value)
        val_lbl.setAlignment(Qt.AlignCenter)
        val_lbl.setStyleSheet(f"color:{ACCENT_GOLD}; font-size:24px; font-weight:700; background:transparent;")
        name_lbl = QLabel(label)
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setStyleSheet("color:rgba(255,255,255,0.6); font-size:11px; background:transparent;")
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
        save_btn.setObjectName("login-btn")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setFixedHeight(50)
        save_btn.clicked.connect(self._save_settings)
        lay.addWidget(save_btn)

        reset_btn = QPushButton("Reset saved credentials")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setFixedHeight(50)
        reset_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{RED};
                           border:1px solid {RED}; border-radius:14px;
                           padding:8px; font-size:12px; font-weight:700; }}
            QPushButton:hover {{ background:{RED}; color:white; }}
        """)
        reset_btn.clicked.connect(self._reset_creds)
        lay.addWidget(reset_btn)

        self._settings_msg = QLabel("")
        self._settings_msg.setStyleSheet(f"color:{ACCENT_GOLD}; font-size:11px; background:transparent;")
        lay.addWidget(self._settings_msg)

        lay.addStretch(1)

        ver = QLabel(f"SteamGuard Loader {APP_VERSION}")
        ver.setStyleSheet("color:rgba(255,255,255,0.4); font-size:10px; background:transparent;")
        lay.addWidget(ver)
        return scroll

    def _make_toggle(self, label, checked):
        row = QFrame()
        row.setStyleSheet(f"QFrame {{ background:{CARD_SURFACE}; border-radius:17px; }}")
        h = QHBoxLayout(row)
        h.setContentsMargins(18, 14, 18, 14)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{TEXT}; font-size:14px; background:transparent;")
        h.addWidget(lbl)
        h.addStretch(1)
        chk = QCheckBox()
        chk.setChecked(checked)
        chk.setCursor(Qt.PointingHandCursor)
        chk.setStyleSheet(f"""
            QCheckBox {{ background:transparent; }}
            QCheckBox::indicator {{ width:44px; height:22px; border-radius:11px;
                                    border:none; background:#2E2E2F; }}
            QCheckBox::indicator:checked {{ background:{ACCENT_GOLD}; }}
        """)
        h.addWidget(chk)
        return row, chk

    def _save_settings(self):
        self._settings["launch_on_startup"] = self._toggle_startup[1].isChecked()
        self._settings["auto_launch_steamguard"] = self._toggle_autolaunch[1].isChecked()
        save_settings(self._settings)
        self._apply_startup(self._settings["launch_on_startup"])
        self._settings_msg.setText("✓ Settings saved.")

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
        self._settings_msg.setText("✓ Saved credentials cleared.")

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
        # Native title bar (NOT frameless). Scale 1445x883 → 1100x670.
        self.setWindowTitle("Rivvak Community")
        self.setMinimumSize(1100, 670)
        self.resize(1100, 670)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(f"background:rgba(15,16,20,245);")  # near-opaque allows acrylic to tint through

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
            pywinstyles.change_header_color(self, BG)
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
    # Apply Windows Acrylic/Mica for real frosted glass effect
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
