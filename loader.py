"""
SteamGuard Loader
=================
A premium PyQt5 front-end for the SteamGuard client, styled after modern gaming
launchers (MY.GAMES). Handles license login, credential persistence
(DPAPI / XOR fallback), a glassmorphism login screen, and a game-launcher style
dashboard (My Tools / Rewards / Referrals / Settings) that launches the main
SteamGuard.exe client.

All networking runs on QThread workers — the UI thread is never blocked.

Backend: rivvak.app (override with the SG_SERVER_URL env var).
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

# ── Colour palette ────────────────────────────────────────────────────────────
BG       = "#080B10"
SURFACE  = "#0D1117"
CARD     = "#161B22"
BORDER   = "#30363D"
ACCENT   = "#23A559"   # green
ACCENT2  = "#58A6FF"   # blue for links / coming-soon
RED      = "#F23F43"
YELLOW   = "#F0B232"
TEXT     = "#E6EDF3"
MUTED    = "#6E7681"
MUTED2   = "#8B949E"

# Discord / YouTube brand colours
DISCORD_BLURPLE = "#5865F2"
YOUTUBE_RED     = "#FF0000"

# Derived shades
ACCENT_HOVER = "#2FBF6B"
ACCENT_DIM   = "#1B8047"

APP_VERSION = "v2.0"

DISCORD_INVITE = "https://discord.gg/RTHM8YhpE"
BRAND_SITE     = "https://rivvak.app"

# Default license/API server. The auth API is served under rivvak.app; override
# via the SG_SERVER_URL env var.
DEFAULT_SERVER_URL = os.environ.get("SG_SERVER_URL", "https://rivvak.app")

# ── Persistence paths ─────────────────────────────────────────────────────────
_APPDATA   = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "SteamGuard"
_CREDS_FILE  = _APPDATA / "loader_creds.bin"
_TOKEN_FILE  = _APPDATA / "loader_token.bin"
_SETTINGS_FILE = _APPDATA / "loader_settings.json"

# XOR fallback key (only used when DPAPI is unavailable, e.g. non-Windows/dev).
_XOR_KEY = b"RivvakSteamGuardLoader-v2-fallback-key-2026"

# ── Brand PNG icons (base64) ──────────────────────────────────────────────────
_DISCORD_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAABdklEQVR4nM2UP0tcQRTFf7PEP0mj"
    "roIYm6hVCrENFtoEYSH4ENIlVoKktFC7FCEQ+A38EFaSIlEEWwOCWKxdlKxJQFwRN/4s3hTPYVx"
    "BG08199x7zlzu3PfgsSOkhNoBTAHTwHoIYSfJvwJqwHdgM4RwmTVUu4CPwFugP9J7wC4wEOMGMA"
    "G8jPEJsAYshhAu0s5WvD+WUrNhtfkAwzN1CKASPT8AT9uPuy2eRQ+C2g38AnofYAjwB3heAWYyZ"
    "i1gC/idETZirpXwVeA16mpmJrMAalWtl/hDtRpzbzK6L6jbCXlcvlb9fENwM3eUaH9UgLGk9R61"
    "rxSPlM4vSmZ9mVGNobYyrX9Ta+qyelXiryz2taZuZHTnQTUz+PvifwVoJuRfik/qLjSAfwnXfAK"
    "8B1Ypnh2KdfgK1IFJYBwYjLlj4CewDYwCC8kF8wCo/fE1T+Ms9tXO21pTO9WDWHuqfjKuU1rYq8"
    "5Z/KLaQp1U36k9d9U+LlwDVADD06LIUmQAAAAASUVORK5CYII="
)
_YOUTUBE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAA+klEQVR4nOXSzSqFYRiF4ev9mCj/"
    "P1MpmZqaKOUcnIADoQydFAZImZkSMpEQysBPexmwS7H39+0YKPfoHazn7mk9L3+d0n4kmcMsxjG"
    "IIfR3mGvhHo+4xkkp5bgtGk6yk5+zlWRIko1fkLVZr7D4ixUuVpiuCR32IJypMFkTWsAqLhsIJy"
    "R56VZKO5n3420meeoSf5Kk1UT4Sbyc5LlDvNXLhgNJ1pI81m14VydMspLkrFvug9t+3GCkU8tJt"
    "rHU4CBwW+GiJtRUBucVdnsYqGNPkrEkBw36qWM/yWiBJAXzmMEURtGHsW+2CO7wigdc4RRHpZQv"
    "3+wf8AbPBCBqSpXODwAAAABJRU5ErkJggg=="
)


def _png_pixmap(b64: str) -> QPixmap:
    """Decode a base64 PNG string into a QPixmap. Returns an empty pixmap on failure."""
    pm = QPixmap()
    try:
        pm.loadFromData(base64.b64decode(b64), "PNG")
    except Exception:
        pass
    return pm


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
#  Custom-drawn widgets
# ══════════════════════════════════════════════════════════════════════════════

class RCLogo(QWidget):
    """RC logo — white 'RC' letters in a rounded green square. QPainter drawn."""

    def __init__(self, size=60, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        rect = QRectF(1, 1, s - 2, s - 2)
        grad = QLinearGradient(0, 0, s, s)
        grad.setColorAt(0, QColor(ACCENT_HOVER))
        grad.setColorAt(1, QColor(ACCENT_DIM))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        radius = s * 0.28
        p.drawRoundedRect(rect, radius, radius)
        # RC text
        p.setPen(QColor("white"))
        f = QFont("Segoe UI", int(s * 0.34))
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "RC")
        p.end()


class SmallLogo(QWidget):
    """Compact RC logo for the title bar (rounded square)."""

    def __init__(self, size=28, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        rect = QRectF(1, 1, s - 2, s - 2)
        grad = QLinearGradient(0, 0, s, s)
        grad.setColorAt(0, QColor(ACCENT_HOVER))
        grad.setColorAt(1, QColor(ACCENT_DIM))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(rect, s * 0.28, s * 0.28)
        p.setPen(QColor("white"))
        f = QFont("Segoe UI", int(s * 0.34))
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, "RC")
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


class Avatar(QWidget):
    """Circular avatar with the first letter of the username."""

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
        grad = QLinearGradient(0, 0, self._size, self._size)
        grad.setColorAt(0, QColor(ACCENT_HOVER))
        grad.setColorAt(1, QColor(ACCENT_DIM))
        p.setBrush(QBrush(grad))
        p.drawEllipse(0, 0, self._size, self._size)
        p.setPen(QColor("white"))
        f = QFont("Segoe UI", int(self._size * 0.42))
        f.setBold(True)
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignCenter, self._letter)
        p.end()


# ── Sidebar icon buttons (QPainter geometric icons) ────────────────────────────

class SideIconButton(QPushButton):
    """A 44px circular icon-only nav button. Icons drawn with QPainter."""

    def __init__(self, kind, tooltip="", accent=ACCENT, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._accent = accent
        self._active = False
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(48, 48)
        self.setToolTip(tooltip)
        self.setStyleSheet("QPushButton { background:transparent; border:none; }")
        self._hover = False

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
        d = 44
        cx, cy = w / 2, h / 2
        circ = QRectF(cx - d / 2, cy - d / 2, d, d)

        # Circle background
        if self._active:
            p.setBrush(QColor(self._accent))
            p.setPen(Qt.NoPen)
            p.drawEllipse(circ)
            icon_color = QColor("white")
        elif self._hover:
            bg = QColor(255, 255, 255, 22)
            p.setBrush(bg)
            p.setPen(Qt.NoPen)
            p.drawEllipse(circ)
            icon_color = QColor(TEXT)
        else:
            icon_color = QColor(MUTED2)

        self._draw_icon(p, cx, cy, icon_color)
        p.end()

    def _draw_icon(self, p, cx, cy, color):
        kind = self._kind
        pen = QPen(color, 2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)

        if kind == "shield":
            r = 11
            path = QPainterPath()
            top = cy - r
            path.moveTo(cx, top)
            path.lineTo(cx + r, top + r * 0.45)
            path.lineTo(cx + r, cy + r * 0.2)
            path.quadTo(cx + r, cy + r * 0.85, cx, cy + r * 1.1)
            path.quadTo(cx - r, cy + r * 0.85, cx - r, cy + r * 0.2)
            path.lineTo(cx - r, top + r * 0.45)
            path.closeSubpath()
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            p.drawPath(path)

        elif kind == "gift":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            box = QRectF(cx - 10, cy - 4, 20, 14)
            p.drawRoundedRect(box, 2, 2)
            # lid
            lid = QRectF(cx - 11, cy - 9, 22, 6)
            p.drawRoundedRect(lid, 2, 2)
            # vertical ribbon
            p.drawLine(int(cx), int(cy - 9), int(cx), int(cy + 10))
            # bow lines
            p.drawLine(int(cx), int(cy - 9), int(cx - 6), int(cy - 13))
            p.drawLine(int(cx), int(cy - 9), int(cx + 6), int(cy - 13))

        elif kind == "chain":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(cx - 11, cy - 4, 12, 12))
            p.drawEllipse(QRectF(cx - 1, cy - 8, 12, 12))

        elif kind == "gear":
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            teeth = QPolygonF()
            outer, inner = 12.0, 8.5
            for i in range(12):
                ang = math.pi * i / 6.0
                rad = outer if i % 2 == 0 else inner
                teeth.append(QPoint(int(cx + rad * math.cos(ang)),
                                    int(cy + rad * math.sin(ang))))
            p.drawPolygon(teeth)
            # hub hole
            p.setBrush(QColor(SURFACE) if not self._active else QColor(ACCENT))
            hole_col = QColor(8, 11, 16) if not self._active else QColor("white")
            p.setBrush(hole_col)
            p.drawEllipse(QRectF(cx - 4, cy - 4, 8, 8))

        elif kind == "logout":
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            # door frame (open right side)
            p.drawLine(int(cx - 8), int(cy - 9), int(cx - 8), int(cy + 9))
            p.drawLine(int(cx - 8), int(cy - 9), int(cx + 1), int(cy - 9))
            p.drawLine(int(cx - 8), int(cy + 9), int(cx + 1), int(cy + 9))
            # arrow shaft
            p.drawLine(int(cx - 2), int(cy), int(cx + 10), int(cy))
            # arrow head
            p.drawLine(int(cx + 10), int(cy), int(cx + 5), int(cy - 5))
            p.drawLine(int(cx + 10), int(cy), int(cx + 5), int(cy + 5))


# ══════════════════════════════════════════════════════════════════════════════
#  Game cards (QPainter gradient backgrounds)
# ══════════════════════════════════════════════════════════════════════════════

class GameCard(QFrame):
    """A MY.GAMES style game card with a painted gradient art background, a dark
    bottom overlay, title/subtitle text, and an action button pill."""

    def __init__(self, title, subtitle, grad_top, grad_bottom, border_color,
                 border_hover, action_text, action_color, action_enabled=True,
                 on_action=None, parent=None):
        super().__init__(parent)
        self._title = title
        self._subtitle = subtitle
        self._grad_top = QColor(grad_top)
        self._grad_bottom = QColor(grad_bottom)
        self._border_color = QColor(border_color)
        self._border_hover = QColor(border_hover)
        self._hovered = False
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(0)
        lay.addStretch(1)

        # Bottom row: text block on the left, action button on the right
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(8)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._title_lbl = QLabel(title.upper())
        self._title_lbl.setStyleSheet(
            f"color:{TEXT}; font-size:14px; font-weight:800; letter-spacing:1px; background:transparent;")
        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet(f"color:{MUTED2}; font-size:10px; background:transparent;")
        text_col.addWidget(self._title_lbl)
        text_col.addWidget(sub_lbl)
        bottom.addLayout(text_col)
        bottom.addStretch(1)

        self.action_btn = QPushButton(action_text)
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.setFixedSize(120, 32)
        self._action_color = action_color
        self._style_action(action_color, action_enabled)
        if action_enabled and on_action is not None:
            self.action_btn.clicked.connect(on_action)
        if not action_enabled:
            self.action_btn.setDisabled(True)
        bottom.addWidget(self.action_btn, alignment=Qt.AlignBottom)

        lay.addLayout(bottom)

    def _style_action(self, color, enabled):
        if enabled:
            self.action_btn.setStyleSheet(f"""
                QPushButton {{ background:{color}; color:white; border:none;
                               border-radius:16px; font-weight:800; font-size:11px;
                               letter-spacing:1px; }}
                QPushButton:hover {{ background:{ACCENT_HOVER}; }}
            """)
        else:
            self.action_btn.setStyleSheet(f"""
                QPushButton {{ background:rgba(88,166,255,0.25); color:{ACCENT2};
                               border:1px solid rgba(88,166,255,0.4);
                               border-radius:16px; font-weight:800; font-size:10px;
                               letter-spacing:1px; }}
            """)

    def set_action_running(self):
        self.action_btn.setText("RUNNING ●")
        self.action_btn.setDisabled(True)
        self.action_btn.setStyleSheet(f"""
            QPushButton {{ background:{ACCENT_DIM}; color:white; border:none;
                           border-radius:16px; font-weight:800; font-size:11px;
                           letter-spacing:1px; }}
        """)

    def reset_action(self, text, color):
        self.action_btn.setText(text)
        self.action_btn.setDisabled(False)
        self._style_action(color, True)

    def enterEvent(self, e):
        self._hovered = True
        self.update()

    def leaveEvent(self, e):
        self._hovered = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = 12

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.setClipPath(path)

        # Art gradient background
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, self._grad_top)
        grad.setColorAt(1, self._grad_bottom)
        p.fillRect(self.rect(), QBrush(grad))

        # Dark gradient overlay at bottom 80px
        oh = min(90, self.height())
        overlay = QLinearGradient(0, self.height() - oh, 0, self.height())
        overlay.setColorAt(0, QColor(0, 0, 0, 0))
        overlay.setColorAt(1, QColor(0, 0, 0, 200))
        p.fillRect(QRectF(0, self.height() - oh, self.width(), oh), QBrush(overlay))

        p.setClipping(False)
        # Border
        bcol = self._border_hover if self._hovered else self._border_color
        pen = QPen(bcol, 1.5 if self._hovered else 1)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, radius, radius)
        p.end()


class ComingSoonCard(QFrame):
    """Full-width coming-soon card: painted grey gradient, centered lock icon,
    'COMING SOON' text and subtitle. Dashed border feel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("background:transparent;")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = 12

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.setClipPath(path)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor("#1a1a1a"))
        grad.setColorAt(1, QColor("#0d1117"))
        p.fillRect(self.rect(), QBrush(grad))
        p.setClipping(False)

        # Dashed border
        pen = QPen(QColor(255, 255, 255, 40), 1.4)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, radius, radius)

        cx = self.width() / 2
        cy = self.height() / 2 - 20

        # Lock icon: rounded shackle + body with keyhole
        lock_col = QColor(MUTED2)
        pen2 = QPen(lock_col, 3)
        pen2.setCapStyle(Qt.RoundCap)
        p.setPen(pen2)
        p.setBrush(Qt.NoBrush)
        # shackle
        p.drawArc(QRectF(cx - 12, cy - 26, 24, 26), 0, 180 * 16)
        # body
        p.setPen(Qt.NoPen)
        p.setBrush(lock_col)
        body = QRectF(cx - 16, cy - 10, 32, 24)
        p.drawRoundedRect(body, 4, 4)
        # keyhole
        p.setBrush(QColor("#1a1a1a"))
        p.drawEllipse(QRectF(cx - 3, cy - 3, 6, 6))
        p.drawRect(QRectF(cx - 1.5, cy, 3, 8))

        # Texts
        p.setPen(QColor(TEXT))
        f = QFont("Segoe UI", 13)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        p.setFont(f)
        p.drawText(QRectF(0, cy + 26, self.width(), 26), Qt.AlignCenter, "COMING SOON")

        p.setPen(QColor(MUTED2))
        f2 = QFont("Segoe UI", 9)
        p.setFont(f2)
        p.drawText(QRectF(0, cy + 52, self.width(), 20), Qt.AlignCenter,
                   "New tool in development — stay tuned")
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  Reusable styled inputs / buttons
# ══════════════════════════════════════════════════════════════════════════════

def make_input(placeholder, password=False):
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setFixedHeight(44)
    if password:
        e.setEchoMode(QLineEdit.Password)
    e.setStyleSheet(f"""
        QLineEdit {{
            background:rgba(255,255,255,0.04);
            color:{TEXT};
            border:1px solid rgba(255,255,255,0.10);
            border-radius:10px;
            padding:0 14px;
            font-size:13px;
        }}
        QLineEdit:focus {{ border:1px solid {ACCENT}; background:rgba(255,255,255,0.06); }}
    """)
    return e


def make_primary_button(text, height=48):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(height)
    b.setStyleSheet(f"""
        QPushButton {{
            background:{ACCENT};
            color:white;
            border:none;
            border-radius:10px;
            font-weight:800;
            font-size:13px;
            letter-spacing:1px;
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


class GradientBackground(QWidget):
    """A widget painting the dark radial gradient (#1a2040 center → #080B10 edges)."""

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        radius = max(self.width(), self.height()) * 0.75
        grad = QRadialGradient(cx, cy, radius)
        grad.setColorAt(0, QColor("#1a2040"))
        grad.setColorAt(1, QColor("#080B10"))
        p.fillRect(self.rect(), QBrush(grad))
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  Custom title bar
# ══════════════════════════════════════════════════════════════════════════════

class TitleBar(QFrame):
    """40px custom title bar with RC logo, title, minimize + close circles."""

    def __init__(self, window, title="SteamGuard", parent=None):
        super().__init__(parent)
        self._win = window
        self._drag_pos = None
        self.setFixedHeight(40)
        self.setStyleSheet("background:rgba(0,0,0,0.6);")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 10, 0)
        lay.setSpacing(10)

        self._logo = SmallLogo(size=26)
        lay.addWidget(self._logo)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color:{TEXT}; font-weight:700; font-size:13px; background:transparent;")
        lay.addWidget(title_lbl)

        lay.addStretch(1)

        self._min_btn = self._mk_btn("—", self._minimize)
        self._close_btn = self._mk_btn("✕", self._close, hover=RED)
        lay.addWidget(self._min_btn)
        lay.addWidget(self._close_btn)

    def _mk_btn(self, text, slot, hover="rgba(255,255,255,0.14)"):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(26, 26)
        b.setStyleSheet(f"""
            QPushButton {{ background:rgba(255,255,255,0.06); color:{MUTED2};
                           border:none; font-size:12px; border-radius:13px; }}
            QPushButton:hover {{ background:{hover}; color:white; }}
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


# ══════════════════════════════════════════════════════════════════════════════
#  Screen 1 — Login (glassmorphism card over radial gradient)
# ══════════════════════════════════════════════════════════════════════════════

class LoginScreen(GradientBackground):
    login_success = pyqtSignal(dict)   # emits {token, discord_username, tier, ...}

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self._win = window
        self._worker = None
        self._settings = load_settings()
        self._drag_pos = None
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.setAlignment(Qt.AlignCenter)

        # Center glass card
        card = QFrame()
        card.setFixedWidth(380)
        card.setStyleSheet("""
            QFrame {
                background:rgba(13,17,23,0.88);
                border:1px solid rgba(255,255,255,0.08);
                border-radius:16px;
            }
        """)
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 180))
        card.setGraphicsEffect(shadow)

        c = QVBoxLayout(card)
        c.setContentsMargins(36, 36, 36, 36)
        c.setSpacing(6)

        # 1. RC Logo (60x60)
        logo = RCLogo(60)
        c.addWidget(logo, alignment=Qt.AlignHCenter)
        c.addSpacing(10)

        # 2. Community label
        community = QLabel("R I V V A K   C O M M U N I T Y")
        community.setAlignment(Qt.AlignCenter)
        community.setStyleSheet(
            f"color:{MUTED}; font-size:11px; letter-spacing:2px; background:transparent;")
        c.addWidget(community)

        # 3. Title
        title = QLabel("SteamGuard")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color:{TEXT}; font-size:24px; font-weight:800; background:transparent;")
        c.addWidget(title)

        # 4. Version subtitle
        badge = QLabel(f"Loader {APP_VERSION}")
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"color:{ACCENT}; font-size:10px; font-weight:700; background:transparent;")
        c.addWidget(badge)

        c.addSpacing(20)

        # 6/7. Inputs
        self._discord = make_input("Discord User ID")
        self._key = make_input("License Key", password=True)
        c.addWidget(self._discord)
        c.addWidget(self._key)

        # 8. Remember me
        self._remember = QCheckBox("Remember me")
        self._remember.setCursor(Qt.PointingHandCursor)
        self._remember.setStyleSheet(f"""
            QCheckBox {{ color:{MUTED2}; font-size:12px; spacing:8px; background:transparent; }}
            QCheckBox::indicator {{ width:16px; height:16px; border-radius:4px;
                                    border:1px solid {BORDER}; background:rgba(255,255,255,0.04); }}
            QCheckBox::indicator:checked {{ background:{ACCENT}; border:1px solid {ACCENT}; }}
        """)
        c.addWidget(self._remember)

        c.addSpacing(4)

        # 9. Login button
        self._login_btn = make_primary_button("LOGIN", 48)
        self._login_btn.clicked.connect(self._on_login)
        c.addWidget(self._login_btn)

        # 10. Spinner (hidden by default)
        self._spinner = Spinner(26)
        self._spinner.hide()
        spin_row = QHBoxLayout()
        spin_row.addStretch(1)
        spin_row.addWidget(self._spinner)
        spin_row.addStretch(1)
        c.addLayout(spin_row)

        # 11. Error label (hidden)
        self._error = QLabel("")
        self._error.setAlignment(Qt.AlignCenter)
        self._error.setWordWrap(True)
        self._error.setStyleSheet(f"color:{RED}; font-size:11px; background:transparent;")
        self._error.hide()
        c.addWidget(self._error)

        # 12. gap
        c.addSpacing(16)

        # 13. Social row
        social = QHBoxLayout()
        social.setSpacing(10)
        discord_btn = self._social_btn(
            "Join Discord", DISCORD_BLURPLE, _png_pixmap(_DISCORD_PNG_B64),
            lambda: webbrowser.open(DISCORD_INVITE))
        youtube_btn = self._social_btn(
            "YouTube", YOUTUBE_RED, _png_pixmap(_YOUTUBE_PNG_B64),
            lambda: webbrowser.open(BRAND_SITE))
        social.addWidget(discord_btn)
        social.addWidget(youtube_btn)
        c.addLayout(social)

        root.addWidget(card, alignment=Qt.AlignCenter)

        # Prefill saved creds
        creds = load_creds()
        if creds:
            self._discord.setText(creds.get("discord_user_id", ""))
            self._key.setText(creds.get("key", ""))
            self._remember.setChecked(True)

    def _social_btn(self, text, color, pixmap, slot):
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(40)
        if not pixmap.isNull():
            b.setIcon(QIcon(pixmap))
            b.setIconSize(QSize(18, 18))
        b.setStyleSheet(f"""
            QPushButton {{ background:{color}; color:white; border:none;
                           border-radius:10px; font-size:12px; font-weight:700;
                           padding-left:6px; }}
            QPushButton:hover {{ background:{color}; }}
        """)
        b.clicked.connect(slot)
        return b

    # Allow dragging the frameless window from anywhere on the login screen
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
#  Screen 2 — Dashboard (MY.GAMES launcher style)
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

    # ── layout ────────────────────────────────────────────────────────────
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(TitleBar(self._win, "SteamGuard"))

        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        main.addWidget(self._build_sidebar())

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background:transparent;")
        self._stack.addWidget(self._build_protection_tab())   # 0
        self._stack.addWidget(self._build_rewards_tab())       # 1
        self._stack.addWidget(self._build_referrals_tab())     # 2
        self._stack.addWidget(self._build_settings_tab())      # 3
        main.addWidget(self._stack, 1)

        root.addLayout(main, 1)

    def _build_sidebar(self):
        bar = QFrame()
        bar.setFixedWidth(72)
        bar.setStyleSheet("background:rgba(8,11,16,0.85);")
        lay = QVBoxLayout(bar)
        lay.setContentsMargins(12, 16, 12, 14)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignHCenter)

        self._nav = []
        for kind, tip, idx in [
            ("shield", "My Tools", 0),
            ("gift", "Rewards", 1),
            ("chain", "Referrals", 2),
            ("gear", "Settings", 3),
        ]:
            item = SideIconButton(kind, tip)
            item.clicked.connect(lambda _=False, i=idx: self._switch_tab(i))
            lay.addWidget(item, alignment=Qt.AlignHCenter)
            self._nav.append(item)
        self._nav[0].setChecked(True)

        lay.addStretch(1)

        logout_btn = SideIconButton("logout", "Logout", accent=RED)
        logout_btn.setCheckable(False)
        logout_btn.clicked.connect(self._on_logout)
        lay.addWidget(logout_btn, alignment=Qt.AlignHCenter)
        return bar

    def _switch_tab(self, idx):
        for i, item in enumerate(self._nav):
            item.setChecked(i == idx)
        self._stack.setCurrentIndex(idx)
        if idx == 1:
            self._load_rewards()
        elif idx == 2:
            self._load_referrals()

    def _content_page(self):
        """A gradient-background page with a vertical layout, returns (widget, layout)."""
        page = GradientBackground()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(28, 22, 28, 18)
        lay.setSpacing(16)
        return page, lay

    def _header(self, text):
        h = QLabel(text)
        h.setStyleSheet(f"color:{TEXT}; font-size:18px; font-weight:800; background:transparent;")
        return h

    # ── Protection tab ("My Tools") ─────────────────────────────────────────
    def _build_protection_tab(self):
        page, lay = self._content_page()

        heading = QLabel("My Tools")
        heading.setAlignment(Qt.AlignCenter)
        heading.setStyleSheet(f"color:{TEXT}; font-size:18px; font-weight:800; background:transparent;")
        lay.addWidget(heading)

        # Cards grid (2 columns)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)

        # Card 1 — SteamGuard
        self._sg_card = GameCard(
            "SteamGuard", "Family Sharing Protection",
            grad_top="#1a3a2a", grad_bottom="#0d1117",
            border_color="rgba(35,165,89,0.3)", border_hover="rgba(35,165,89,0.8)",
            action_text="PLAY", action_color=ACCENT,
            action_enabled=True, on_action=self._on_launch,
        )
        self._sg_card.setMinimumHeight(240)
        grid.addWidget(self._sg_card, 0, 0)

        # Card 2 — Roblox Tool
        self._roblox_card = GameCard(
            "Roblox Tool", "Auto-Farm & Utilities",
            grad_top="#1a1a3a", grad_bottom="#0d1117",
            border_color="rgba(88,166,255,0.3)", border_hover="rgba(88,166,255,0.8)",
            action_text="COMING SOON", action_color=ACCENT2,
            action_enabled=False,
        )
        self._roblox_card.setMinimumHeight(240)
        grid.addWidget(self._roblox_card, 0, 1)

        # Card 3 — Coming Soon (full width)
        coming = ComingSoonCard()
        coming.setMinimumHeight(200)
        grid.addWidget(coming, 1, 0, 1, 2)

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid, 1)

        # User info strip
        lay.addWidget(self._build_user_strip())
        return page

    def _build_user_strip(self):
        strip = QFrame()
        strip.setFixedHeight(48)
        strip.setStyleSheet("background:rgba(0,0,0,0.4); border-radius:10px;")
        h = QHBoxLayout(strip)
        h.setContentsMargins(12, 6, 12, 6)
        h.setSpacing(10)

        username = self._session.get("discord_username", "User")
        self._avatar = Avatar(username[0] if username else "?", 32)
        h.addWidget(self._avatar)

        name_lbl = QLabel(username if len(username) <= 18 else username[:17] + "…")
        name_lbl.setStyleSheet(f"color:{TEXT}; font-size:12px; font-weight:700; background:transparent;")
        h.addWidget(name_lbl)

        tier = self._session.get("tier", "FREE")
        tier_color = YELLOW if tier == "PREMIUM" else BORDER
        self._tier_lbl = QLabel(tier)
        self._tier_lbl.setStyleSheet(f"""
            QLabel {{ color:{'#1C2128' if tier=='PREMIUM' else TEXT};
                      background:{tier_color}; border-radius:9px;
                      padding:2px 10px; font-size:9px; font-weight:800; }}
        """)
        h.addWidget(self._tier_lbl)

        h.addStretch(1)

        self._time_lbl = QLabel("⏰ —")
        self._time_lbl.setStyleSheet(f"""
            QLabel {{ color:{TEXT}; background:rgba(35,165,89,0.18);
                      border:1px solid rgba(35,165,89,0.4); border-radius:12px;
                      padding:4px 14px; font-size:11px; font-weight:700; }}
        """)
        h.addWidget(self._time_lbl)
        return strip

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
            self._time_lbl.setText("⚠ SteamGuard.exe not found")
            self._time_lbl.setStyleSheet(f"""
                QLabel {{ color:{RED}; background:rgba(242,63,67,0.18);
                          border:1px solid rgba(242,63,67,0.4); border-radius:12px;
                          padding:4px 14px; font-size:11px; font-weight:700; }}
            """)
            return
        try:
            self._sg_process = subprocess.Popen([exe])
        except Exception as e:
            self._time_lbl.setText(f"⚠ Launch failed")
            return

        self._sg_card.set_action_running()
        self._proc_timer.start(1000)

    def _check_process(self):
        if self._sg_process is None:
            self._proc_timer.stop()
            return
        if self._sg_process.poll() is not None:
            # Process exited — re-enable button
            self._proc_timer.stop()
            self._sg_process = None
            self._sg_card.reset_action("PLAY", ACCENT)

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
        # Auto-launch if enabled
        if self._settings.get("auto_launch_steamguard"):
            QTimer.singleShot(500, self._on_launch)

    # ── Rewards tab ───────────────────────────────────────────────────────
    def _build_rewards_tab(self):
        page, lay = self._content_page()

        top = QHBoxLayout()
        top.addWidget(self._header("Rewards"))
        top.addStretch(1)
        daily = QPushButton("Claim Daily")
        daily.setCursor(Qt.PointingHandCursor)
        daily.setFixedHeight(32)
        daily.setStyleSheet(f"""
            QPushButton {{ background:{ACCENT}; color:white; border:none;
                           border-radius:6px; padding:0 16px; font-weight:700;
                           font-size:11px; }}
            QPushButton:hover {{ background:{ACCENT_HOVER}; }}
        """)
        daily.clicked.connect(self._claim_daily)
        top.addWidget(daily)
        lay.addLayout(top)

        self._rewards_scroll = QScrollArea()
        self._rewards_scroll.setWidgetResizable(True)
        self._rewards_scroll.setStyleSheet("QScrollArea { border:none; background:transparent; }")
        self._rewards_scroll.viewport().setStyleSheet("background:transparent;")
        self._rewards_inner = QWidget()
        self._rewards_inner.setStyleSheet("background:transparent;")
        self._rewards_lay = QGridLayout(self._rewards_inner)
        self._rewards_lay.setContentsMargins(0, 0, 0, 0)
        self._rewards_lay.setHorizontalSpacing(12)
        self._rewards_lay.setVerticalSpacing(12)
        self._rewards_lay.setAlignment(Qt.AlignTop)
        self._rewards_scroll.setWidget(self._rewards_inner)
        lay.addWidget(self._rewards_scroll, 1)

        self._rewards_msg = QLabel("Loading rewards…")
        self._rewards_msg.setStyleSheet(f"color:{MUTED2}; font-size:12px; background:transparent;")
        self._rewards_lay.addWidget(self._rewards_msg, 0, 0)
        return page

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
            msg.setStyleSheet(f"color:{MUTED2}; font-size:12px; background:transparent;")
            self._rewards_lay.addWidget(msg, 0, 0)
            return
        rewards = data.get("rewards") or data.get("items") or []
        if not rewards:
            msg = QLabel("No rewards available right now.")
            msg.setStyleSheet(f"color:{MUTED2}; font-size:12px; background:transparent;")
            self._rewards_lay.addWidget(msg, 0, 0)
            return
        for i, r in enumerate(rewards):
            row, col = divmod(i, 2)
            self._rewards_lay.addWidget(self._reward_card(r), row, col)

    def _reward_card(self, r):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background:rgba(255,255,255,0.04);
                border:1px solid rgba(255,255,255,0.08);
                border-radius:10px;
            }
        """)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(str(r.get("name", "Reward")))
        name.setStyleSheet(f"color:{TEXT}; font-size:13px; font-weight:700; background:transparent;")
        info.addWidget(name)
        hours = r.get("hours") or r.get("reward_hours")
        if hours:
            badge = QLabel(f"+{hours}h")
            badge.setStyleSheet(f"color:{ACCENT}; font-size:11px; font-weight:800; background:transparent;")
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
                               border-radius:6px; padding:0 16px; font-weight:700;
                               font-size:11px; }}
                QPushButton:hover {{ background:{ACCENT_HOVER}; }}
            """)
            rid = r.get("id") or r.get("name")
            btn.clicked.connect(lambda _=False, i=rid: self._claim_reward(i))
            lay.addWidget(btn)
        else:
            cd = QLabel(f"{cooldown}m" if cooldown else "Locked")
            cd.setStyleSheet(f"""
                QLabel {{ color:{MUTED2}; background:rgba(255,255,255,0.06);
                          border-radius:12px; padding:4px 12px; font-size:10px;
                          font-weight:700; }}
            """)
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
        page, lay = self._content_page()
        lay.addWidget(self._header("Referrals"))

        link_lbl = QLabel("Your referral link")
        link_lbl.setStyleSheet(f"color:{MUTED2}; font-size:11px; font-weight:700; background:transparent;")
        lay.addWidget(link_lbl)

        link_row = QHBoxLayout()
        self._ref_link = QLineEdit("Loading…")
        self._ref_link.setReadOnly(True)
        self._ref_link.setFixedHeight(40)
        self._ref_link.setStyleSheet(f"""
            QLineEdit {{ background:rgba(255,255,255,0.04); color:{ACCENT2};
                         border:1px solid rgba(255,255,255,0.08);
                         border-radius:10px; padding:0 14px; font-size:12px; }}
        """)
        link_row.addWidget(self._ref_link, 1)
        copy_link = self._copy_button(lambda: self._copy(self._ref_link.text()))
        link_row.addWidget(copy_link)
        lay.addLayout(link_row)

        # Stats
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self._stat_valid = self._stat_card("Valid", "0")
        self._stat_pending = self._stat_card("Pending", "0")
        self._stat_earned = self._stat_card("Earned (h)", "0")
        stats_row.addWidget(self._stat_valid[0])
        stats_row.addWidget(self._stat_pending[0])
        stats_row.addWidget(self._stat_earned[0])
        lay.addLayout(stats_row)

        # Invite message
        msg_lbl = QLabel("Invite message")
        msg_lbl.setStyleSheet(f"color:{MUTED2}; font-size:11px; font-weight:700; background:transparent;")
        lay.addWidget(msg_lbl)

        msg_row = QHBoxLayout()
        self._invite_msg = QLineEdit(
            "Join SteamGuard — the Steam Family Sharing unlocker! " + DISCORD_INVITE)
        self._invite_msg.setReadOnly(True)
        self._invite_msg.setFixedHeight(40)
        self._invite_msg.setStyleSheet(f"""
            QLineEdit {{ background:rgba(255,255,255,0.04); color:{TEXT};
                         border:1px solid rgba(255,255,255,0.08);
                         border-radius:10px; padding:0 14px; font-size:11px; }}
        """)
        msg_row.addWidget(self._invite_msg, 1)
        copy_msg = self._copy_button(lambda: self._copy(self._invite_msg.text()))
        msg_row.addWidget(copy_msg)
        lay.addLayout(msg_row)

        lay.addStretch(1)
        return page

    def _copy_button(self, slot):
        b = QPushButton("Copy")
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(40)
        b.setStyleSheet(f"""
            QPushButton {{ background:rgba(255,255,255,0.06); color:{TEXT};
                           border:1px solid rgba(255,255,255,0.10);
                           border-radius:10px; padding:0 18px; font-size:11px; }}
            QPushButton:hover {{ border:1px solid {ACCENT}; }}
        """)
        b.clicked.connect(slot)
        return b

    def _stat_card(self, label, value):
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background:rgba(255,255,255,0.04);
                border:1px solid rgba(255,255,255,0.08);
                border-radius:10px;
            }
        """)
        v = QVBoxLayout(card)
        v.setContentsMargins(10, 14, 10, 14)
        v.setSpacing(2)
        val_lbl = QLabel(value)
        val_lbl.setAlignment(Qt.AlignCenter)
        val_lbl.setStyleSheet(f"color:{ACCENT}; font-size:22px; font-weight:800; background:transparent;")
        name_lbl = QLabel(label)
        name_lbl.setAlignment(Qt.AlignCenter)
        name_lbl.setStyleSheet(f"color:{MUTED2}; font-size:10px; background:transparent;")
        v.addWidget(val_lbl)
        v.addWidget(name_lbl)
        return card, val_lbl

    def _load_referrals(self):
        if not self._token:
            return
        # Fix: always ensure a referral code exists before showing.
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
        page, lay = self._content_page()
        lay.addWidget(self._header("Settings"))

        # Toggles
        self._toggle_startup = self._make_toggle(
            "Launch on startup", self._settings.get("launch_on_startup", False))
        lay.addWidget(self._toggle_startup[0])

        self._toggle_autolaunch = self._make_toggle(
            "Auto-launch SteamGuard", self._settings.get("auto_launch_steamguard", False))
        lay.addWidget(self._toggle_autolaunch[0])

        # Save button
        save_btn = make_primary_button("Save Settings", height=44)
        save_btn.clicked.connect(self._save_settings)
        lay.addWidget(save_btn)

        # Reset credentials (red)
        reset_btn = QPushButton("Reset saved credentials")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setFixedHeight(44)
        reset_btn.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{RED};
                           border:1px solid {RED}; border-radius:10px;
                           padding:8px; font-size:12px; font-weight:700; }}
            QPushButton:hover {{ background:{RED}; color:white; }}
        """)
        reset_btn.clicked.connect(self._reset_creds)
        lay.addWidget(reset_btn)

        self._settings_msg = QLabel("")
        self._settings_msg.setStyleSheet(f"color:{ACCENT}; font-size:11px; background:transparent;")
        lay.addWidget(self._settings_msg)

        lay.addStretch(1)

        ver = QLabel(f"SteamGuard Loader {APP_VERSION}")
        ver.setStyleSheet(f"color:{MUTED}; font-size:10px; background:transparent;")
        lay.addWidget(ver)
        return page

    def _make_toggle(self, label, checked):
        row = QFrame()
        row.setStyleSheet("""
            QFrame { background:rgba(255,255,255,0.04);
                     border:1px solid rgba(255,255,255,0.08);
                     border-radius:10px; }
        """)
        h = QHBoxLayout(row)
        h.setContentsMargins(14, 10, 14, 10)
        lbl = QLabel(label)
        lbl.setStyleSheet(f"color:{TEXT}; font-size:13px; background:transparent;")
        h.addWidget(lbl)
        h.addStretch(1)
        chk = QCheckBox()
        chk.setChecked(checked)
        chk.setCursor(Qt.PointingHandCursor)
        chk.setStyleSheet(f"""
            QCheckBox {{ background:transparent; }}
            QCheckBox::indicator {{ width:40px; height:20px; border-radius:10px;
                                    background:{BORDER}; }}
            QCheckBox::indicator:checked {{ background:{ACCENT}; }}
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
#  Main window with fade transitions
# ══════════════════════════════════════════════════════════════════════════════

class LoaderWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setFixedSize(1100, 720)
        self.setStyleSheet(f"background:{BG};")
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
