"""
SteamGuard Loader
=================
A polished PyQt5 front-end for the SteamGuard client. Handles license login,
credential persistence (DPAPI / XOR fallback), an animated login screen, and a
full dashboard (Protection / Rewards / Referrals / Settings) that launches the
main SteamGuard.exe client.

All networking runs on QThread workers — the UI thread is never blocked.

Backend: rivvak.app (override with the SG_SERVER_URL env var or Settings tab).
"""

import sys
import os
import json
import math
import base64
import hashlib
import webbrowser
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

from PyQt5.QtCore import (
    Qt, QSize, QTimer, QThread, pyqtSignal, QPoint, QRectF, QPropertyAnimation,
    QEasingCurve, pyqtProperty, QObject,
)
from PyQt5.QtGui import (
    QColor, QPainter, QPen, QBrush, QPolygonF, QFont, QFontMetrics, QIcon,
    QLinearGradient, QPainterPath,
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QHBoxLayout, QCheckBox, QFrame, QGraphicsOpacityEffect, QStackedWidget,
    QScrollArea, QComboBox, QSizePolicy, QGraphicsDropShadowEffect,
)

# ── Colour palette ────────────────────────────────────────────────────────────
BG       = "#0D1117"
SURFACE  = "#161B22"
CARD     = "#1C2128"
BORDER   = "#30363D"
ACCENT   = "#23A559"   # green
ACCENT2  = "#58A6FF"   # blue for links
RED      = "#F23F43"
YELLOW   = "#F0B232"
TEXT     = "#E6EDF3"
MUTED    = "#8B949E"

# Derived shades
ACCENT_HOVER = "#2FBF6B"
ACCENT_DIM   = "#1B8047"

APP_VERSION = "v2.0"

DISCORD_INVITE = "https://discord.gg/RTHM8YhpE"
BRAND_SITE     = "https://rivvak.app"

# Default license/API server. The auth API is served under rivvak.app; override
# via the SG_SERVER_URL env var or the Settings tab.
DEFAULT_SERVER_URL = os.environ.get("SG_SERVER_URL", "https://rivvak.app")

# ── Persistence paths ─────────────────────────────────────────────────────────
_APPDATA   = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "SteamGuard"
_CREDS_FILE  = _APPDATA / "loader_creds.bin"
_TOKEN_FILE  = _APPDATA / "loader_token.bin"
_SETTINGS_FILE = _APPDATA / "loader_settings.json"

# XOR fallback key (only used when DPAPI is unavailable, e.g. non-Windows/dev).
_XOR_KEY = b"RivvakSteamGuardLoader-v2-fallback-key-2026"


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
        "server_url": DEFAULT_SERVER_URL,
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


# ══════════════════════════════════════════════════════════════════════════════
#  Custom-drawn widgets
# ══════════════════════════════════════════════════════════════════════════════

class TitleBar(QFrame):
    """36px custom title bar with RC logo, title, version, minimize + close."""

    def __init__(self, window, title="SteamGuard", version=APP_VERSION, parent=None):
        super().__init__(parent)
        self._win = window
        self._drag_pos = None
        self.setFixedHeight(36)
        self.setStyleSheet(f"background:{SURFACE};")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(8)

        self._logo = LogoWidget(size=20)
        lay.addWidget(self._logo)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color:{TEXT}; font-weight:600; font-size:12px;")
        lay.addWidget(title_lbl)

        ver_lbl = QLabel(version)
        ver_lbl.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        lay.addWidget(ver_lbl)

        lay.addStretch(1)

        self._min_btn = self._mk_btn("—", self._minimize)
        self._close_btn = self._mk_btn("✕", self._close, hover=RED)
        lay.addWidget(self._min_btn)
        lay.addWidget(self._close_btn)

    def _mk_btn(self, text, slot, hover=BORDER):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(28, 24)
        b.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{MUTED};
                           border:none; font-size:13px; border-radius:4px; }}
            QPushButton:hover {{ background:{hover}; color:{TEXT}; }}
        """)
        b.clicked.connect(slot)
        return b

    def _minimize(self):
        self._win.showMinimized()

    def _close(self):
        self._win.close()

    # Dragging
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPos() - self._win.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self._win.move(e.globalPos() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


class LogoWidget(QWidget):
    """Small RC hexagon/shield logo drawn with QPainter."""

    def __init__(self, size=20, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        # Hexagon shield
        poly = QPolygonF([
            QPoint(int(s * 0.5), int(s * 0.05)),
            QPoint(int(s * 0.95), int(s * 0.28)),
            QPoint(int(s * 0.95), int(s * 0.72)),
            QPoint(int(s * 0.5), int(s * 0.95)),
            QPoint(int(s * 0.05), int(s * 0.72)),
            QPoint(int(s * 0.05), int(s * 0.28)),
        ])
        grad = QLinearGradient(0, 0, s, s)
        grad.setColorAt(0, QColor(ACCENT))
        grad.setColorAt(1, QColor(ACCENT_DIM))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawPolygon(poly)
        # RC text
        p.setPen(QColor("white"))
        f = QFont("Segoe UI", int(s * 0.32))
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "RC")
        p.end()


class PulsingShield(QWidget):
    """Large animated green pulsing shield with 'RC' text (login hero)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(120)
        self.setMinimumWidth(120)
        self._scale = 1.0
        self._anim = QPropertyAnimation(self, b"scaleFactor")
        self._anim.setStartValue(1.0)
        self._anim.setKeyValueAt(0.5, 1.05)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(2000)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.start()

    def getScale(self):
        return self._scale

    def setScale(self, v):
        self._scale = v
        self.update()

    scaleFactor = pyqtProperty(float, fget=getScale, fset=setScale)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx = self.width() / 2
        cy = self.height() / 2
        base = 46.0
        r = base * self._scale

        # Outer glow ring
        glow_alpha = int(60 * (1.0 - (self._scale - 1.0) / 0.05 * 0.5))
        glow = QColor(ACCENT)
        glow.setAlpha(max(20, min(80, glow_alpha)))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QRectF(cx - r * 1.35, cy - r * 1.35, r * 2.7, r * 2.7))

        # Shield shape
        path = QPainterPath()
        top = cy - r
        path.moveTo(cx, top)
        path.lineTo(cx + r, top + r * 0.45)
        path.lineTo(cx + r, cy + r * 0.25)
        path.quadTo(cx + r, cy + r * 0.9, cx, cy + r * 1.15)
        path.quadTo(cx - r, cy + r * 0.9, cx - r, cy + r * 0.25)
        path.lineTo(cx - r, top + r * 0.45)
        path.closeSubpath()

        grad = QLinearGradient(cx, top, cx, cy + r)
        grad.setColorAt(0, QColor(ACCENT_HOVER))
        grad.setColorAt(1, QColor(ACCENT_DIM))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(ACCENT), 2))
        p.drawPath(path)

        # RC text
        p.setPen(QColor("white"))
        f = QFont("Segoe UI", 20)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(cx - r, cy - r * 0.4, r * 2, r * 1.2),
                   Qt.AlignCenter, "RC")
        p.end()


class Spinner(QWidget):
    """Rotating arc spinner drawn with QPainter, updated every 50ms."""

    def __init__(self, size=28, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self.show()
        self._timer.start(50)

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(3, 3, self.width() - 6, self.height() - 6)
        pen = QPen(QColor(ACCENT), 3)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, -self._angle * 16, 270 * 16)
        p.end()


class StatusDot(QWidget):
    """A small coloured status dot with a label (e.g. FIREWALL ●)."""

    def __init__(self, label, color=MUTED, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        self._text = QLabel(label)
        self._text.setStyleSheet(f"color:{MUTED}; font-size:10px; font-weight:600;")
        self._dot = _Dot(color)
        lay.addWidget(self._text)
        lay.addWidget(self._dot)

    def set_color(self, color):
        self._dot.set_color(color)


class _Dot(QWidget):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color = color

    def set_color(self, color):
        self._color = color
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(1, 1, 8, 8)
        p.end()


class Avatar(QWidget):
    """Circular avatar with the first letter of the username."""

    def __init__(self, letter="?", size=44, parent=None):
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
        grad = QLinearGradient(0, 0, self._size, self._size)
        grad.setColorAt(0, QColor(ACCENT_HOVER))
        grad.setColorAt(1, QColor(ACCENT_DIM))
        p.setBrush(QBrush(grad))
        p.drawEllipse(0, 0, self._size, self._size)
        p.setPen(QColor("white"))
        f = QFont("Segoe UI", int(self._size * 0.4))
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self._letter)
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  Reusable styled inputs / buttons
# ══════════════════════════════════════════════════════════════════════════════

def make_input(placeholder, password=False):
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFixedHeight(42)
    if password:
        e.setEchoMode(QLineEdit.Password)
    e.setStyleSheet(f"""
        QLineEdit {{
            background:{BG};
            color:{TEXT};
            border:1px solid {BORDER};
            border-radius:8px;
            padding:0 12px;
            font-size:13px;
        }}
        QLineEdit:focus {{ border:1px solid {ACCENT}; }}
    """)
    return e


def make_primary_button(text, height=44):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    b.setStyleSheet(f"""
        QPushButton {{
            background:{ACCENT};
            color:white;
            border:none;
            border-radius:8px;
            font-weight:700;
            font-size:14px;
        }}
        QPushButton:hover {{ background:{ACCENT_HOVER}; }}
        QPushButton:disabled {{ background:{BORDER}; color:{MUTED}; }}
    """)
    return b


def make_link(text, color=ACCENT2):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFlat(True)
    b.setStyleSheet(f"""
        QPushButton {{ background:transparent; color:{color};
                       border:none; font-size:11px; }}
        QPushButton:hover {{ color:{TEXT}; text-decoration:underline; }}
    """)
    return b


# ══════════════════════════════════════════════════════════════════════════════
#  Screen 1 — Login
# ══════════════════════════════════════════════════════════════════════════════

class LoginScreen(QWidget):
    login_success = pyqtSignal(dict)   # emits {token, discord_username, tier, ...}

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

        root.addWidget(TitleBar(self._win, "SteamGuard", APP_VERSION))

        body = QVBoxLayout()
        body.setContentsMargins(36, 12, 36, 20)
        body.setSpacing(6)
        body.setAlignment(Qt.AlignTop)

        # Hero shield
        self._shield = PulsingShield()
        body.addWidget(self._shield, alignment=Qt.AlignHCenter)

        community = QLabel("R I V V A K   C O M M U N I T Y")
        community.setAlignment(Qt.AlignCenter)
        community.setStyleSheet(f"color:{MUTED}; font-size:10px; letter-spacing:2px;")
        body.addWidget(community)

        title = QLabel("SteamGuard Loader")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color:{TEXT}; font-size:18px; font-weight:700;")
        body.addWidget(title)

        badge = QLabel(APP_VERSION)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:700;")
        body.addWidget(badge)

        body.addSpacing(10)

        # Inputs
        self._discord = make_input("Discord User ID")
        self._key = make_input("License Key", password=True)
        body.addWidget(self._discord)
        body.addWidget(self._key)

        # Remember me
        self._remember = QCheckBox("Remember me")
        self._remember.setCursor(Qt.PointingHandCursor)
        self._remember.setStyleSheet(f"""
            QCheckBox {{ color:{MUTED}; font-size:12px; spacing:8px; }}
            QCheckBox::indicator {{ width:16px; height:16px; border-radius:4px;
                                    border:1px solid {BORDER}; background:{BG}; }}
            QCheckBox::indicator:checked {{ background:{ACCENT}; border:1px solid {ACCENT}; }}
        """)
        body.addWidget(self._remember)

        body.addSpacing(4)

        # Login button
        self._login_btn = make_primary_button("LOGIN")
        self._login_btn.clicked.connect(self._on_login)
        body.addWidget(self._login_btn)

        # Spinner (hidden by default)
        self._spinner = Spinner(26)
        self._spinner.hide()
        spin_row = QHBoxLayout()
        spin_row.addStretch(1)
        spin_row.addWidget(self._spinner)
        spin_row.addStretch(1)
        body.addLayout(spin_row)

        # Error label (hidden by default)
        self._error = QLabel("")
        self._error.setAlignment(Qt.AlignCenter)
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color:{RED}; font-size:11px;")
        self._error.hide()
        body.addWidget(self._error)

        body.addStretch(1)

        # Bottom links
        links = QHBoxLayout()
        links.setSpacing(4)
        get_key = make_link("Get a Key", ACCENT2)
        get_key.clicked.connect(lambda: webbrowser.open(DISCORD_INVITE))
        sep = QLabel("|")
        sep.setStyleSheet(f"color:{BORDER}; font-size:11px;")
        site = make_link("rivvak.app", MUTED)
        site.clicked.connect(lambda: webbrowser.open(BRAND_SITE))
        links.addStretch(1)
        links.addWidget(get_key)
        links.addWidget(sep)
        links.addWidget(site)
        links.addStretch(1)
        body.addLayout(links)

        root.addLayout(body)

        # Prefill saved creds
        creds = load_creds()
        if creds:
            self._discord.setText(creds.get("discord_user_id", ""))
            self._key.setText(creds.get("key", ""))
            self._remember.setChecked(True)

    def try_auto_login(self):
        """Called on startup — auto-login if saved creds + token exist."""
        creds = load_creds()
        if creds.get("discord_user_id") and creds.get("key"):
            self._on_login(auto=True)

    def _set_busy(self, busy: bool):
        self._login_btn.setDisabled(busy)
        self._login_btn.setText("SIGNING IN…" if busy else "LOGIN")
        if busy:
            self._error.hide()
            self._spinner.start()
        else:
            self._spinner.stop()

    def _show_error(self, msg: str):
        self._error.setText(msg)
        self._error.show()

    def _on_login(self, auto=False):
        discord_id = self._discord.text().strip()
        key = self._key.text().strip()
        if not discord_id:
            self._show_error("Please enter your Discord User ID.")
            return
        if not key:
            self._show_error("Please enter your license key.")
            return

        self._set_busy(True)
        server = self._settings.get("server_url", DEFAULT_SERVER_URL)
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
            "server_url": self._settings.get("server_url", DEFAULT_SERVER_URL),
        }
        self.login_success.emit(session)


# ══════════════════════════════════════════════════════════════════════════════
#  Dashboard — nav items
# ══════════════════════════════════════════════════════════════════════════════

class NavItem(QPushButton):
    def __init__(self, icon, label, parent=None):
        super().__init__(f"  {icon}  {label}", parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        self.setFixedHeight(38)
        self.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{MUTED}; border:none;
                text-align:left; padding-left:10px; font-size:12px;
                border-radius:6px;
            }}
            QPushButton:hover {{ background:{CARD}; color:{TEXT}; }}
            QPushButton:checked {{ background:{CARD}; color:{TEXT};
                                   font-weight:700; border-left:3px solid {ACCENT}; }}
        """)


# ── Rewards / Referrals data workers ────────────────────────────────────────

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
#  Screen 2 — Dashboard
# ══════════════════════════════════════════════════════════════════════════════

class Dashboard(QWidget):
    logout = pyqtSignal()

    def __init__(self, window, session: dict, parent=None):
        super().__init__(parent)
        self._win = window
        self._session = session
        self._token = session.get("token", "")
        self._server = session.get("server_url", DEFAULT_SERVER_URL).rstrip("/")
        self._settings = load_settings()
        self._sg_process = None
        self._proc_timer = QTimer(self)
        self._proc_timer.timeout.connect(self._check_process)
        self._workers = []   # keep references alive
        self._build()
        self._load_overview()

    # ── layout ────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(TitleBar(self._win, "SteamGuard", APP_VERSION))

        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._build_sidebar())

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background:{CARD};")
        self._stack.addWidget(self._build_protection_tab())   # 0
        self._stack.addWidget(self._build_rewards_tab())       # 1
        self._stack.addWidget(self._build_referrals_tab())     # 2
        self._stack.addWidget(self._build_settings_tab())      # 3
        main.addWidget(self._stack, 1)

        root.addLayout(main, 1)

    def _build_sidebar(self):
        bar = QFrame()
        bar.setFixedWidth(140)
        bar.setStyleSheet(f"background:{SURFACE};")
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(10, 14, 10, 12)
        lay.setSpacing(8)

        username = self._session.get("discord_username", "User")
        self._avatar = Avatar(username[0] if username else "?", 44)
        lay.addWidget(self._avatar, alignment=Qt.AlignHCenter)

        name_lbl = QLabel(username if len(username) <= 14 else username[:13] + "…")
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setStyleSheet(f"color:{TEXT}; font-size:12px; font-weight:600;")
        lay.addWidget(name_lbl)

        tier = self._session.get("tier", "FREE")
        tier_color = YELLOW if tier == "PREMIUM" else MUTED
        self._tier_lbl = QLabel(tier)
        self._tier_lbl.setAlignment(Qt.AlignCenter)
        self._tier_lbl.setStyleSheet(f"""
            QLabel {{ color:{'#1C2128' if tier=='PREMIUM' else TEXT};
                      background:{tier_color}; border-radius:9px;
                      padding:2px 10px; font-size:9px; font-weight:700; }}
        """)
        tier_row = QHBoxLayout()
        tier_row.addStretch(1)
        tier_row.addWidget(self._tier_lbl)
        tier_row.addStretch(1)
        lay.addLayout(tier_row)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{BORDER};")
        lay.addWidget(sep)

        # Nav
        self._nav = []
        for icon, label, idx in [
            ("🛡", "Protection", 0),
            ("🎁", "Rewards", 1),
            ("🔗", "Referrals", 2),
            ("⚙️", "Settings", 3),
        ]:
            item = NavItem(icon, label)
            item.clicked.connect(lambda _=False, i=idx: self._switch_tab(i))
            lay.addWidget(item)
            self._nav.append(item)
        self._nav[0].setChecked(True)

        lay.addStretch(1)

        logout_btn = QPushButton("  ⎋  Logout")
        logout_btn.setCursor(Qt.PointingHandCursor)
        logout_btn.setFixedHeight(34)
        logout_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{RED}; border:none;
                           text-align:left; padding-left:10px; font-size:12px;
                           border-radius:6px; }}
            QPushButton:hover {{ background:{CARD}; }}
        """)
        logout_btn.clicked.connect(self._on_logout)
        lay.addWidget(logout_btn)
        return bar

    def _switch_tab(self, idx):
        for i, item in enumerate(self._nav):
            item.setChecked(i == idx)
        self._stack.setCurrentIndex(idx)
        if idx == 1:
            self._load_rewards()
        elif idx == 2:
            self._load_referrals()

    # ── Protection tab ──────────────────────────────────────────────────────
    def _build_protection_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        heading = QLabel("Protection")
        heading.setStyleSheet(f"color:{TEXT}; font-size:16px; font-weight:700;")
        lay.addWidget(heading)

        self._launch_btn = QPushButton("LAUNCH STEAMGUARD")
        self._launch_btn.setCursor(Qt.PointingHandCursor)
        self._launch_btn.setFixedHeight(60)
        self._launch_btn.setStyleSheet(f"""
            QPushButton {{ background:{ACCENT}; color:white; border:none;
                           border-radius:10px; font-size:16px; font-weight:700; }}
            QPushButton:hover {{ background:{ACCENT_HOVER}; }}
            QPushButton:disabled {{ background:{BORDER}; color:{MUTED}; }}
        """)
        self._launch_btn.clicked.connect(self._on_launch)
        lay.addWidget(self._launch_btn)

        # Status dots row
        status_row = QHBoxLayout()
        status_row.setSpacing(16)
        self._dot_firewall = StatusDot("FIREWALL", MUTED)
        self._dot_network = StatusDot("NETWORK", ACCENT)
        self._dot_library = StatusDot("LIBRARY", MUTED)
        status_row.addWidget(self._dot_firewall)
        status_row.addWidget(self._dot_network)
        status_row.addWidget(self._dot_library)
        status_row.addStretch(1)
        lay.addLayout(status_row)

        # Time remaining card
        self._time_card = QFrame()
        self._time_card.setStyleSheet(f"background:{SURFACE}; border-radius:8px;")
        tc_lay = QHBoxLayout(self._time_card)
        tc_lay.setContentsMargins(14, 12, 14, 12)
        self._time_lbl = QLabel("⏰  Time Remaining: —")
        self._time_lbl.setStyleSheet(f"color:{TEXT}; font-size:13px; font-weight:600;")
        tc_lay.addWidget(self._time_lbl)
        tc_lay.addStretch(1)
        lay.addWidget(self._time_card)

        # Recent activity
        act_lbl = QLabel("Recent Activity")
        act_lbl.setStyleSheet(f"color:{MUTED}; font-size:11px; font-weight:600;")
        lay.addWidget(act_lbl)

        self._activity = QScrollArea()
        self._activity.setWidgetResizable(True)
        self._activity.setStyleSheet(f"""
            QScrollArea {{ background:{SURFACE}; border:1px solid {BORDER};
                           border-radius:8px; }}
        """)
        self._activity_inner = QWidget()
        self._activity_inner.setStyleSheet(f"background:{SURFACE};")
        self._activity_lay = QVBoxLayout(self._activity_inner)
        self._activity_lay.setContentsMargins(10, 8, 10, 8)
        self._activity_lay.setSpacing(3)
        self._activity_lay.setAlignment(Qt.AlignTop)
        self._activity.setWidget(self._activity_inner)
        lay.addWidget(self._activity, 1)

        self._set_activity([
            "Loader initialized.",
            "Session token loaded.",
            "Awaiting launch…",
        ])
        return w

    def _set_activity(self, lines):
        # clear
        while self._activity_lay.count():
            item = self._activity_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for line in lines[-5:]:
            lbl = QLabel(line)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color:{MUTED}; font-family:Consolas,monospace; font-size:11px;")
            self._activity_lay.addWidget(lbl)

    def _find_steamguard(self):
        """Return path to SteamGuard.exe next to the loader or on PATH."""
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).parent
        else:
            base = Path(__file__).parent
        local = base / "SteamGuard.exe"
        if local.exists():
            return str(local)
        # On PATH
        import shutil
        found = shutil.which("SteamGuard.exe")
        if found:
            return found
        return None

    def _on_launch(self):
        exe = self._find_steamguard()
        if not exe:
            self._set_activity([
                "ERROR: SteamGuard.exe not found in the same folder",
            ])
            self._time_lbl.setText("⚠  SteamGuard.exe not found in the same folder")
            self._time_lbl.setStyleSheet(f"color:{RED}; font-size:13px; font-weight:600;")
            return
        try:
            self._sg_process = subprocess.Popen([exe])
        except Exception as e:
            self._set_activity([f"ERROR launching SteamGuard: {e}"])
            return

        self._launch_btn.setText("✓ RUNNING")
        self._launch_btn.setDisabled(True)
        self._dot_firewall.set_color(ACCENT)
        self._dot_library.set_color(ACCENT)
        self._set_activity([
            "Launching SteamGuard.exe…",
            "Process started.",
            "Firewall protection engaged.",
        ])
        self._proc_timer.start(1000)

    def _check_process(self):
        if self._sg_process is None:
            self._proc_timer.stop()
            return
        if self._sg_process.poll() is not None:
            # Process exited — re-enable button
            self._proc_timer.stop()
            self._sg_process = None
            self._launch_btn.setText("LAUNCH STEAMGUARD")
            self._launch_btn.setDisabled(False)
            self._dot_firewall.set_color(MUTED)
            self._dot_library.set_color(MUTED)
            self._set_activity(["SteamGuard exited.", "Ready to relaunch."])

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
                self._time_lbl.setText(f"⏰  Time Remaining: {h}h {m}m")
            except Exception:
                pass
        logs = data.get("recent_activity") or data.get("logs")
        if isinstance(logs, list) and logs:
            self._set_activity([str(x) for x in logs])
        # Auto-launch if enabled
        if self._settings.get("auto_launch_steamguard"):
            QTimer.singleShot(500, self._on_launch)

    # ── Rewards tab ───────────────────────────────────────────────────────
    def _build_rewards_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        heading = QLabel("Rewards")
        heading.setStyleSheet(f"color:{TEXT}; font-size:16px; font-weight:700;")
        top.addWidget(heading)
        top.addStretch(1)
        daily = QPushButton("Claim Daily")
        daily.setCursor(Qt.PointingHandCursor)
        daily.setFixedHeight(30)
        daily.setStyleSheet(f"""
            QPushButton {{ background:{ACCENT}; color:white; border:none;
                           border-radius:6px; padding:0 14px; font-weight:700;
                           font-size:11px; }}
            QPushButton:hover {{ background:{ACCENT_HOVER}; }}
        """)
        daily.clicked.connect(self._claim_daily)
        top.addWidget(daily)
        lay.addLayout(top)

        self._rewards_scroll = QScrollArea()
        self._rewards_scroll.setWidgetResizable(True)
        self._rewards_scroll.setStyleSheet("QScrollArea { border:none; }")
        self._rewards_inner = QWidget()
        self._rewards_lay = QVBoxLayout(self._rewards_inner)
        self._rewards_lay.setContentsMargins(0, 0, 0, 0)
        self._rewards_lay.setSpacing(8)
        self._rewards_lay.setAlignment(Qt.AlignTop)
        self._rewards_scroll.setWidget(self._rewards_inner)
        lay.addWidget(self._rewards_scroll, 1)

        self._rewards_msg = QLabel("Loading rewards…")
        self._rewards_msg.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        self._rewards_lay.addWidget(self._rewards_msg)
        return w

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
            msg.setStyleSheet(f"color:{MUTED}; font-size:12px;")
            self._rewards_lay.addWidget(msg)
            return
        rewards = data.get("rewards") or data.get("items") or []
        if not rewards:
            msg = QLabel("No rewards available right now.")
            msg.setStyleSheet(f"color:{MUTED}; font-size:12px;")
            self._rewards_lay.addWidget(msg)
            return
        for r in rewards:
            self._rewards_lay.addWidget(self._reward_card(r))

    def _reward_card(self, r):
        card = QFrame()
        card.setStyleSheet(f"background:{SURFACE}; border:1px solid {BORDER}; border-radius:8px;")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)

        icon = QLabel(r.get("icon", "🎁"))
        icon.setStyleSheet("font-size:20px;")
        lay.addWidget(icon)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(str(r.get("name", "Reward")))
        name.setStyleSheet(f"color:{TEXT}; font-size:13px; font-weight:600;")
        info.addWidget(name)
        hours = r.get("hours") or r.get("reward_hours")
        if hours:
            badge = QLabel(f"+{hours}h")
            badge.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:700;")
            info.addWidget(badge)
        lay.addLayout(info)
        lay.addStretch(1)

        available = r.get("available", True)
        cooldown = r.get("cooldown_minutes") or r.get("cooldown")
        if available:
            btn = QPushButton("Claim")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setStyleSheet(f"""
                QPushButton {{ background:{ACCENT}; color:white; border:none;
                               border-radius:6px; padding:0 14px; font-weight:700;
                               font-size:11px; }}
                QPushButton:hover {{ background:{ACCENT_HOVER}; }}
            """)
            rid = r.get("id") or r.get("name")
            btn.clicked.connect(lambda _=False, i=rid: self._claim_reward(i))
            lay.addWidget(btn)
        else:
            cd = QLabel(f"{cooldown}m cooldown" if cooldown else "Unavailable")
            cd.setStyleSheet(f"color:{MUTED}; font-size:11px;")
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
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        heading = QLabel("Referrals")
        heading.setStyleSheet(f"color:{TEXT}; font-size:16px; font-weight:700;")
        lay.addWidget(heading)

        link_lbl = QLabel("Your referral link")
        link_lbl.setStyleSheet(f"color:{MUTED}; font-size:11px; font-weight:600;")
        lay.addWidget(link_lbl)

        link_row = QHBoxLayout()
        self._ref_link = QLineEdit("Loading…")
        self._ref_link.setReadOnly(True)
        self._ref_link.setFixedHeight(38)
        self._ref_link.setStyleSheet(f"""
            QLineEdit {{ background:{BG}; color:{ACCENT2}; border:1px solid {BORDER};
                         border-radius:8px; padding:0 12px; font-size:12px; }}
        """)
        link_row.addWidget(self._ref_link, 1)
        copy_link = QPushButton("Copy")
        copy_link.setCursor(Qt.PointingHandCursor)
        copy_link.setFixedHeight(38)
        copy_link.setStyleSheet(f"""
            QPushButton {{ background:{SURFACE}; color:{TEXT}; border:1px solid {BORDER};
                           border-radius:8px; padding:0 16px; font-size:11px; }}
            QPushButton:hover {{ border:1px solid {ACCENT}; }}
        """)
        copy_link.clicked.connect(lambda: self._copy(self._ref_link.text()))
        link_row.addWidget(copy_link)
        lay.addLayout(link_row)

        # Stats
        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)
        self._stat_valid = self._stat_card("Valid", "0")
        self._stat_pending = self._stat_card("Pending", "0")
        self._stat_earned = self._stat_card("Earned (h)", "0")
        stats_row.addWidget(self._stat_valid[0])
        stats_row.addWidget(self._stat_pending[0])
        stats_row.addWidget(self._stat_earned[0])
        lay.addLayout(stats_row)

        # Invite message
        msg_lbl = QLabel("Invite message")
        msg_lbl.setStyleSheet(f"color:{MUTED}; font-size:11px; font-weight:600;")
        lay.addWidget(msg_lbl)

        msg_row = QHBoxLayout()
        self._invite_msg = QLineEdit("Join SteamGuard — the Steam Family Sharing unlocker! " + DISCORD_INVITE)
        self._invite_msg.setReadOnly(True)
        self._invite_msg.setFixedHeight(38)
        self._invite_msg.setStyleSheet(f"""
            QLineEdit {{ background:{BG}; color:{TEXT}; border:1px solid {BORDER};
                         border-radius:8px; padding:0 12px; font-size:11px; }}
        """)
        msg_row.addWidget(self._invite_msg, 1)
        copy_msg = QPushButton("Copy")
        copy_msg.setCursor(Qt.PointingHandCursor)
        copy_msg.setFixedHeight(38)
        copy_msg.setStyleSheet(f"""
            QPushButton {{ background:{SURFACE}; color:{TEXT}; border:1px solid {BORDER};
                           border-radius:8px; padding:0 16px; font-size:11px; }}
            QPushButton:hover {{ border:1px solid {ACCENT}; }}
        """)
        copy_msg.clicked.connect(lambda: self._copy(self._invite_msg.text()))
        msg_row.addWidget(copy_msg)
        lay.addLayout(msg_row)

        lay.addStretch(1)
        return w

    def _stat_card(self, label, value):
        card = QFrame()
        card.setStyleSheet(f"background:{SURFACE}; border:1px solid {BORDER}; border-radius:8px;")
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 12, 10, 12)
        v.setSpacing(2)
        val_lbl = QLabel(value)
        val_lbl.setAlignment(Qt.AlignCenter)
        val_lbl.setStyleSheet(f"color:{ACCENT}; font-size:20px; font-weight:700;")
        name_lbl = QLabel(label)
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setStyleSheet(f"color:{MUTED}; font-size:10px;")
        v.addWidget(val_lbl)
        v.addWidget(name_lbl)
        return card, val_lbl

    def _load_referrals(self):
        if not self._token:
            return
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
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(14)

        heading = QLabel("Settings")
        heading.setStyleSheet(f"color:{TEXT}; font-size:16px; font-weight:700;")
        lay.addWidget(heading)

        # Toggles
        self._toggle_startup = self._make_toggle(
            "Launch on startup", self._settings.get("launch_on_startup", False))
        lay.addWidget(self._toggle_startup[0])

        self._toggle_autolaunch = self._make_toggle(
            "Auto-launch SteamGuard", self._settings.get("auto_launch_steamguard", False))
        lay.addWidget(self._toggle_autolaunch[0])

        # Theme selector
        theme_row = QHBoxLayout()
        theme_lbl = QLabel("Theme")
        theme_lbl.setStyleSheet(f"color:{TEXT}; font-size:13px;")
        theme_row.addWidget(theme_lbl)
        theme_row.addStretch(1)
        self._theme_combo = QComboBox()
        self._theme_combo.addItems(["Dark", "Darker"])
        self._theme_combo.setCurrentText(self._settings.get("theme", "Dark"))
        self._theme_combo.setFixedWidth(120)
        self._theme_combo.setStyleSheet(f"""
            QComboBox {{ background:{BG}; color:{TEXT}; border:1px solid {BORDER};
                         border-radius:6px; padding:4px 8px; font-size:12px; }}
            QComboBox QAbstractItemView {{ background:{SURFACE}; color:{TEXT};
                         selection-background-color:{ACCENT}; }}
        """)
        theme_row.addWidget(self._theme_combo)
        lay.addLayout(theme_row)

        # Server URL
        url_lbl = QLabel("Server URL (advanced)")
        url_lbl.setStyleSheet(f"color:{MUTED}; font-size:11px; font-weight:600;")
        lay.addWidget(url_lbl)
        self._server_input = QLineEdit(self._settings.get("server_url", DEFAULT_SERVER_URL))
        self._server_input.setFixedHeight(38)
        self._server_input.setStyleSheet(f"""
            QLineEdit {{ background:{BG}; color:{TEXT}; border:1px solid {BORDER};
                         border-radius:8px; padding:0 12px; font-size:12px; }}
            QLineEdit:focus {{ border:1px solid {ACCENT}; }}
        """)
        lay.addWidget(self._server_input)

        # Save button
        save_btn = make_primary_button("Save Settings", height=38)
        save_btn.clicked.connect(self._save_settings)
        lay.addWidget(save_btn)

        # Reset credentials
        reset_btn = QPushButton("Reset saved credentials")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setFixedHeight = 38
        reset_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{RED};
                           border:1px solid {RED}; border-radius:8px;
                           padding:8px; font-size:12px; }}
            QPushButton:hover {{ background:{RED}; color:white; }}
        """)
        reset_btn.clicked.connect(self._reset_creds)
        lay.addWidget(reset_btn)

        self._settings_msg = QLabel("")
        self._settings_msg.setStyleSheet(f"color:{ACCENT}; font-size:11px;")
        lay.addWidget(self._settings_msg)

        lay.addStretch(1)
        return w

    def _make_toggle(self, label, checked):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{TEXT}; font-size:13px;")
        h.addWidget(lbl)
        h.addStretch(1)
        chk = QCheckBox()
        chk.setChecked(checked)
        chk.setCursor(Qt.PointingHandCursor)
        chk.setStyleSheet(f"""
            QCheckBox::indicator {{ width:40px; height:20px; border-radius:10px;
                                    background:{BORDER}; }}
            QCheckBox::indicator:checked {{ background:{ACCENT}; }}
        """)
        h.addWidget(chk)
        return row, chk

    def _save_settings(self):
        self._settings["launch_on_startup"] = self._toggle_startup[1].isChecked()
        self._settings["auto_launch_steamguard"] = self._toggle_autolaunch[1].isChecked()
        self._settings["theme"] = self._theme_combo.currentText()
        self._settings["server_url"] = self._server_input.text().strip() or DEFAULT_SERVER_URL
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
#  Main window with fade transitions
# ══════════════════════════════════════════════════════════════════════════════

class LoaderWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setFixedSize(440, 560)
        self.setStyleSheet(f"background:{BG};")
        # Rounded / bordered container feel
        self.setAttribute(Qt.WA_TranslucentBackground, False)

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

        self._login = LoginScreen(self)
        self._login.login_success.connect(self._go_dashboard)
        self._stack.addWidget(self._login)

        self._dashboard = None
        self._center()

    def _center(self):
        try:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move((screen.width() - self.width()) // 2,
                      (screen.height() - self.height()) // 2)
        except Exception:
            pass

    def _fade_to(self, widget):
        """Fade the stack to the given widget over 200ms."""
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
            # A debugger or RE tool is attached — refuse to run.
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
    app.setApplicationName("SteamGuard Loader")
    win = LoaderWindow()
    win.show()
    # Attempt auto-login after the window is shown
    QTimer.singleShot(300, win._login.try_auto_login)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
