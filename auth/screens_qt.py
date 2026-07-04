"""
PySide6 login / activation screens for SteamGuard.

Replaces the tkinter screens in auth/screens.py. Preserves the full preflight
flow (TOS gate → cached session → verify → activation window) and the same
config / DPAPI persistence. Only the UI layer is rewritten.
"""

import os
import json
import webbrowser
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTextEdit, QCheckBox, QFrame, QGraphicsOpacityEffect, QDialog,
)
from PySide6.QtCore import Qt, QThread, Signal, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QColor, QPixmap, QIcon

# ─────────────────────────────────────────────────────────────────────────────
# Dark gaming palette (matches the main window)
# ─────────────────────────────────────────────────────────────────────────────
C_BG        = "#0B0F1A"
C_SURFACE   = "#131929"
C_CARD      = "#1A2238"
C_ACCENT    = "#7C5CFC"
C_ACCENT2   = "#5B8AF0"
C_SUCCESS   = "#22D3A5"
C_WARNING   = "#F59E0B"
C_ERROR     = "#EF4444"
C_TEXT      = "#F1F5F9"
C_TEXT_DIM  = "#94A3B8"
C_DISCORD   = "#5865F2"

# ── Config / persistence ──────────────────────────────────────────────────────
_APPDATA     = Path(os.environ.get("APPDATA", str(Path.home()))) / "SteamGuard"
_CONFIG_FILE = _APPDATA / "config.json"

TOS_VERSION = "1.1"

TOS_TEXT = """STEAMGUARD — END USER LICENSE AGREEMENT & TERMS OF SERVICE
Version 1.1 — Effective upon acceptance

PLEASE READ THESE TERMS CAREFULLY BEFORE USING STEAMGUARD.
BY CLICKING "I ACCEPT", YOU AGREE TO BE BOUND BY THESE TERMS.

THIS SOFTWARE IS PROVIDED FOR EDUCATIONAL AND RESEARCH PURPOSES ONLY.

1. LICENSE GRANT
   Subject to your compliance with these Terms, you are granted a limited,
   personal, non-exclusive, non-transferable, revocable license to install
   and use SteamGuard on one (1) personal computer that you own or control,
   strictly for educational, research, and personal learning purposes.

2. EDUCATIONAL USE ONLY
   SteamGuard is intended solely as an educational tool. You may not use the
   Software to violate any third-party terms of service, circumvent security
   measures, or engage in any unlawful or prohibited activity.

3. RESTRICTIONS
   You may NOT copy, distribute, sell, sublicense, or transfer the Software or
   your license key; reverse engineer or decompile it; create derivative works;
   remove proprietary notices; or share your license key publicly.

4. DISCORD MEMBERSHIP REQUIREMENT
   Your license is contingent on maintaining active membership in the
   designated Discord server and holding the required role.

5. HARDWARE BINDING
   Your license key is bound to the first machine it is activated on. The
   Software collects a one-way hardware identifier hash for this purpose.

6. DISCLAIMER OF WARRANTIES
   THE SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND. YOU USE THE
   SOFTWARE ENTIRELY AT YOUR OWN RISK.

7. LIMITATION OF LIABILITY
   TO THE MAXIMUM EXTENT PERMITTED BY LAW, THE DEVELOPERS SHALL NOT BE LIABLE
   FOR ANY DAMAGES ARISING FROM YOUR USE OF THE SOFTWARE.

8. STEAM & VALVE DISCLAIMER
   STEAMGUARD IS NOT AFFILIATED WITH VALVE OR STEAM. USE MAY VIOLATE VALVE'S
   SUBSCRIBER AGREEMENT. YOU ASSUME ALL RISK OF ACCOUNT SUSPENSION OR BAN.

9. NO REFUNDS / TERMINATION
   All purchases are final. This license terminates automatically on breach.

10. GOVERNING LAW
    Disputes are resolved through binding individual arbitration. You waive any
    right to participate in class actions.

BY CLICKING "I ACCEPT" YOU CONFIRM:
  • You have read, understood, and agree to all Terms above.
  • You are at least 18 years of age or have parental consent.
  • You will use the Software only for educational and lawful purposes.
  • You accept all risks described herein and release us from liability.

IF YOU DO NOT AGREE TO THESE TERMS, DO NOT USE THE SOFTWARE.
"""


def _load_config() -> dict:
    try:
        if _CONFIG_FILE.exists():
            return json.loads(_CONFIG_FILE.read_text("utf-8"))
    except Exception:
        pass
    return {}


def _save_config(cfg: dict):
    try:
        _APPDATA.mkdir(parents=True, exist_ok=True)
        _CONFIG_FILE.write_text(json.dumps(cfg, indent=2), "utf-8")
    except Exception:
        pass


def _tos_accepted() -> bool:
    return _load_config().get("tos_version") == TOS_VERSION


# ── DPAPI helpers (unchanged) ─────────────────────────────────────────────────

def _dpapi_protect(data: bytes) -> bytes:
    try:
        import win32crypt
        return win32crypt.CryptProtectData(data, "SteamGuard-Session", None, None, None, 0)
    except Exception:
        return data


# ═════════════════════════════════════════════════════════════════════════════
# Shared frameless title bar
# ═════════════════════════════════════════════════════════════════════════════

class _MiniTitleBar(QWidget):
    def __init__(self, parent, title="SteamGuard"):
        super().__init__(parent)
        self._win = parent
        self._drag = None
        self.setFixedHeight(36)
        self.setStyleSheet(f"background-color: {C_BG};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(8)

        icon = QLabel()
        icon_path = str(Path(__file__).parent.parent / "icon.png")
        if os.path.exists(icon_path):
            icon.setPixmap(QPixmap(icon_path).scaled(18, 18, Qt.KeepAspectRatio,
                                                     Qt.SmoothTransformation))
        else:
            icon.setText("RC")
            icon.setStyleSheet(f"color:{C_ACCENT};font-weight:700;font-size:12px;")
        lay.addWidget(icon)
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color:{C_TEXT};font-size:11px;font-weight:600;")
        lay.addWidget(lbl)
        lay.addStretch(1)

        close = QPushButton("\u2715")
        close.setFixedSize(26, 22)
        close.setCursor(Qt.PointingHandCursor)
        close.setStyleSheet(
            "QPushButton{background:transparent;color:#94A3B8;border:none;font-size:12px;}"
            "QPushButton:hover{background:#EF4444;border-radius:6px;color:white;}")
        close.clicked.connect(self._win.reject if isinstance(self._win, QDialog) else self._win.close)
        lay.addWidget(close)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self._win.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None


# ═════════════════════════════════════════════════════════════════════════════
# TOS dialog
# ═════════════════════════════════════════════════════════════════════════════

class TOSDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.accepted_tos = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setFixedSize(560, 600)
        self.setStyleSheet(f"QDialog{{background:{C_BG};}}")
        self._build()
        self._center()

    def _center(self):
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2,
                  scr.center().y() - self.height() // 2)

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(_MiniTitleBar(self, "SteamGuard — Terms of Service"))

        body = QVBoxLayout()
        body.setContentsMargins(24, 16, 24, 20)
        body.setSpacing(12)

        title = QLabel("Terms of Service")
        title.setStyleSheet(f"color:{C_TEXT};font-size:18px;font-weight:700;")
        body.addWidget(title)
        sub = QLabel("Scroll to the bottom and check the box to continue.")
        sub.setStyleSheet(f"color:{C_WARNING};font-size:12px;")
        body.addWidget(sub)

        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setPlainText(TOS_TEXT)
        self._text.setStyleSheet(
            f"QTextEdit{{background:{C_CARD};color:{C_TEXT_DIM};border:1px solid rgba(255,255,255,0.06);"
            "border-radius:8px;font-family:Consolas,monospace;font-size:12px;padding:10px;}"
            "QScrollBar:vertical{background:#0D1117;width:6px;border-radius:3px;}"
            "QScrollBar::handle:vertical{background:#2D3748;border-radius:3px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
        self._text.verticalScrollBar().valueChanged.connect(self._on_scroll)
        body.addWidget(self._text, 1)

        self._chk = QCheckBox("I have read, understood, and agree to the Terms of Service")
        self._chk.setEnabled(False)
        self._chk.setStyleSheet(
            f"QCheckBox{{color:{C_TEXT_DIM};font-size:12px;}}"
            f"QCheckBox:disabled{{color:#475569;}}"
            "QCheckBox::indicator{width:16px;height:16px;border-radius:4px;"
            "border:1px solid #2D3748;background:#1A2238;}"
            f"QCheckBox::indicator:checked{{background:{C_ACCENT};border-color:{C_ACCENT};}}")
        self._chk.stateChanged.connect(self._on_check)
        body.addWidget(self._chk)

        row = QHBoxLayout()
        decline = QPushButton("Decline & Exit")
        decline.setCursor(Qt.PointingHandCursor)
        decline.setStyleSheet(
            "QPushButton{background:#1A2238;color:#EF4444;border:none;border-radius:8px;"
            "padding:10px 18px;font-weight:600;}QPushButton:hover{background:rgba(239,68,68,0.12);}")
        decline.clicked.connect(self.reject)
        self._accept_btn = QPushButton("I Accept")
        self._accept_btn.setEnabled(False)
        self._accept_btn.setCursor(Qt.PointingHandCursor)
        self._accept_btn.setStyleSheet(
            "QPushButton{background:#334155;color:white;border:none;border-radius:8px;"
            "padding:10px 24px;font-weight:700;}"
            f"QPushButton:enabled{{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {C_ACCENT},stop:1 {C_ACCENT2});}}")
        self._accept_btn.clicked.connect(self._accept)
        row.addWidget(decline)
        row.addStretch(1)
        row.addWidget(self._accept_btn)
        body.addLayout(row)
        outer.addLayout(body, 1)

    def _on_scroll(self, value):
        sb = self._text.verticalScrollBar()
        if sb.maximum() == 0 or value >= sb.maximum() - 4:
            self._chk.setEnabled(True)

    def _on_check(self, _state):
        self._accept_btn.setEnabled(self._chk.isChecked())

    def _accept(self):
        cfg = _load_config()
        cfg["tos_version"] = TOS_VERSION
        cfg["tos_accepted_at"] = datetime.now().isoformat()
        _save_config(cfg)
        self.accepted_tos = True
        self.accept()


# ═════════════════════════════════════════════════════════════════════════════
# Activation worker (QThread → keeps UI responsive during the network call)
# ═════════════════════════════════════════════════════════════════════════════

class _ActivateWorker(QThread):
    done = Signal(object)  # AuthResult

    def __init__(self, key, discord_id):
        super().__init__()
        self._key = key
        self._discord_id = discord_id

    def run(self):
        try:
            from auth.client import activate
            result = activate(self._key, self._discord_id)
        except Exception as e:
            try:
                from auth.client import AuthResult
                result = AuthResult(ok=False, error=f"Client crash: {e}")
            except Exception:
                class _R:
                    ok = False
                    error = f"Client crash: {e}"
                    session_token = ""
                    token_expires = ""
                    remaining_hours = None
                result = _R()
        self.done.emit(result)


# ═════════════════════════════════════════════════════════════════════════════
# License / Sign-in window
# ═════════════════════════════════════════════════════════════════════════════

class LoginWindow(QDialog):
    def __init__(self, prefill_key: str = "", error: str = ""):
        super().__init__()
        self.session = None
        self._busy = False
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setFixedSize(420, 520)
        self.setStyleSheet(f"QDialog{{background:{C_BG};}}")
        icon_path = str(Path(__file__).parent.parent / "icon.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self._build(prefill_key, error)
        self._center()
        self._fade_in()

    def _center(self):
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2,
                  scr.center().y() - self.height() // 2)

    def _fade_in(self):
        self.setWindowOpacity(0.0)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(320)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.start()

    def _build(self, prefill_key, error):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(_MiniTitleBar(self, "SteamGuard — Sign in"))

        body = QVBoxLayout()
        body.setContentsMargins(36, 18, 36, 24)
        body.setSpacing(12)

        # Logo / heading
        logo = QLabel()
        icon_path = str(Path(__file__).parent.parent / "icon.png")
        if os.path.exists(icon_path):
            logo.setPixmap(QPixmap(icon_path).scaled(56, 56, Qt.KeepAspectRatio,
                                                    Qt.SmoothTransformation))
            logo.setAlignment(Qt.AlignCenter)
            body.addWidget(logo)

        title = QLabel("SteamGuard")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color:{C_TEXT};font-size:20px;font-weight:700;")
        body.addWidget(title)
        sub = QLabel("Sign in with Discord or enter your license key")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet(f"color:{C_TEXT_DIM};font-size:12px;")
        body.addWidget(sub)
        body.addSpacing(6)

        # Discord login button
        discord_btn = QPushButton("  Continue with Discord")
        discord_btn.setCursor(Qt.PointingHandCursor)
        discord_btn.setMinimumHeight(42)
        discord_btn.setStyleSheet(
            f"QPushButton{{background:{C_DISCORD};color:white;border:none;border-radius:8px;"
            "font-size:13px;font-weight:700;}"
            "QPushButton:hover{background:#4752C4;}")
        discord_btn.clicked.connect(lambda: webbrowser.open("https://discord.gg/RTHM8YhpE"))
        body.addWidget(discord_btn)

        # Divider
        div_row = QHBoxLayout()
        for _ in range(2):
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setStyleSheet("color:#2D3748;background:#2D3748;max-height:1px;")
        div_lbl = QLabel("or")
        div_lbl.setStyleSheet(f"color:{C_TEXT_DIM};font-size:11px;")
        l1 = QFrame(); l1.setFrameShape(QFrame.HLine); l1.setStyleSheet("background:#2D3748;max-height:1px;")
        l2 = QFrame(); l2.setFrameShape(QFrame.HLine); l2.setStyleSheet("background:#2D3748;max-height:1px;")
        div_row.addWidget(l1, 1)
        div_row.addWidget(div_lbl)
        div_row.addWidget(l2, 1)
        body.addLayout(div_row)

        input_style = (
            f"QLineEdit{{background:{C_CARD};color:{C_TEXT};border:1px solid #2D3748;"
            "border-radius:8px;padding:10px 12px;font-size:13px;}"
            f"QLineEdit:focus{{border-color:{C_ACCENT};}}")

        key_lbl = QLabel("License Key")
        key_lbl.setStyleSheet(f"color:{C_TEXT_DIM};font-size:11px;font-weight:600;")
        body.addWidget(key_lbl)
        self._key_edit = QLineEdit(prefill_key)
        self._key_edit.setPlaceholderText("XXXX-XXXX-XXXX-XXXX")
        self._key_edit.setStyleSheet(input_style)
        self._key_edit.textEdited.connect(self._auto_format_key)
        body.addWidget(self._key_edit)

        did_lbl = QLabel("Discord User ID")
        did_lbl.setStyleSheet(f"color:{C_TEXT_DIM};font-size:11px;font-weight:600;")
        body.addWidget(did_lbl)
        self._discord_edit = QLineEdit()
        self._discord_edit.setPlaceholderText("17–19 digit Discord User ID")
        self._discord_edit.setStyleSheet(input_style)
        body.addWidget(self._discord_edit)

        self._status = QLabel(f"\u2717 {error}" if error else "")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"color:{C_ERROR if error else C_TEXT_DIM};font-size:11px;")
        body.addWidget(self._status)

        self._signin_btn = QPushButton("Sign in")
        self._signin_btn.setCursor(Qt.PointingHandCursor)
        self._signin_btn.setMinimumHeight(44)
        self._signin_btn.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {C_ACCENT},stop:1 {C_ACCENT2});color:white;border:none;border-radius:8px;"
            "font-size:14px;font-weight:700;}"
            "QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8B6EFF,stop:1 #6B9AF8);}"
            "QPushButton:disabled{background:#334155;color:#94A3B8;}")
        self._signin_btn.clicked.connect(self._on_signin)
        body.addWidget(self._signin_btn)

        body.addStretch(1)

        get_key = QPushButton("Don't have a key? Get one")
        get_key.setCursor(Qt.PointingHandCursor)
        get_key.setFlat(True)
        get_key.setStyleSheet(
            f"QPushButton{{background:transparent;color:{C_ACCENT};border:none;font-size:12px;}}"
            "QPushButton:hover{color:#9B7DFF;text-decoration:underline;}")
        get_key.clicked.connect(lambda: webbrowser.open("https://discord.gg/RTHM8YhpE"))
        body.addWidget(get_key, alignment=Qt.AlignCenter)

        outer.addLayout(body, 1)

    def _auto_format_key(self, _text=None):
        raw = self._key_edit.text().upper().replace("-", "")
        if len(raw) > 4:
            raw = raw[:4] + "-" + raw[4:]
        if len(raw) > 13:
            raw = raw[:13] + "-" + raw[13:]
        if len(raw) > 22:
            raw = raw[:22] + "-" + raw[22:]
        if len(raw) > 31:
            raw = raw[:31] + "-" + raw[31:39]
        self._key_edit.setText(raw)

    def _set_status(self, msg, color=C_TEXT_DIM):
        self._status.setText(msg)
        self._status.setStyleSheet(f"color:{color};font-size:11px;")

    def _on_signin(self):
        if self._busy:
            return
        key = self._key_edit.text().strip()
        discord_id = self._discord_edit.text().strip()
        if len(key) < 10:
            self._set_status("Please enter your license key.", C_ERROR)
            return
        if not discord_id.isdigit():
            self._set_status("Discord User ID must be a number (17–19 digits).", C_ERROR)
            return
        self._busy = True
        self._signin_btn.setEnabled(False)
        self._signin_btn.setText("Signing in…")
        self._set_status("Contacting license server…", C_TEXT_DIM)
        self._worker = _ActivateWorker(key, discord_id)
        self._worker.done.connect(lambda r, k=key, d=discord_id: self._on_result(r, k, d))
        self._worker.start()

    def _on_result(self, result, key, discord_id):
        self._busy = False
        self._signin_btn.setEnabled(True)
        self._signin_btn.setText("Sign in")
        if getattr(result, "ok", False):
            try:
                from auth.cache import save_session
                save_session(result.session_token, result.token_expires, key, discord_id)
            except Exception:
                pass
            cfg = _load_config()
            cfg["license_key"] = key
            cfg["discord_user_id"] = discord_id
            _save_config(cfg)
            try:
                import base64
                protected = _dpapi_protect(result.session_token.encode("utf-8"))
                cfg2 = _load_config()
                cfg2["session_token_dpapi"] = base64.b64encode(protected).decode("ascii")
                _save_config(cfg2)
            except Exception:
                pass
            self._set_status("\u2713 Activated successfully!", C_SUCCESS)
            self.session = {
                "session_token": result.session_token,
                "token_expires": result.token_expires,
                "key": key,
                "discord_user_id": discord_id,
            }
            QPropertyAnimation  # keep import referenced
            from PySide6.QtCore import QTimer
            QTimer.singleShot(700, self.accept)
        else:
            self._set_status(f"\u2717 {getattr(result, 'error', 'Verification failed')}", C_ERROR)


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point — preserves the original preflight flow
# ═════════════════════════════════════════════════════════════════════════════

def run_preflight() -> dict:
    """TOS gate → cached session → verify → activation window.

    Returns a session dict or raises SystemExit(0) if the user declines / aborts.
    Assumes a QApplication already exists (the main entry point creates it).
    """
    app = QApplication.instance() or QApplication([])

    from auth.cache import load_session, session_needs_refresh, save_session
    from auth.client import verify

    if not _tos_accepted():
        tos = TOSDialog()
        if tos.exec() != QDialog.Accepted or not tos.accepted_tos:
            raise SystemExit(0)

    cfg = _load_config()
    saved_key = cfg.get("license_key", "")
    saved_discord_id = cfg.get("discord_user_id", "")

    cached = load_session()
    if cached and not session_needs_refresh():
        return cached

    if saved_key and saved_discord_id:
        try:
            result = verify(saved_key, saved_discord_id)
        except Exception as e:
            class _R:
                ok = False
                error = f"Verification error: {e}"
            result = _R()
        if getattr(result, "ok", False):
            save_session(result.session_token, result.token_expires, saved_key, saved_discord_id)
            return {
                "session_token": result.session_token,
                "token_expires": result.token_expires,
                "key": saved_key,
                "discord_user_id": saved_discord_id,
            }
        error_msg = getattr(result, "error", "") or "Verification failed"
        win = LoginWindow(prefill_key=saved_key, error=error_msg)
        if win.exec() != QDialog.Accepted or win.session is None:
            raise SystemExit(0)
        return win.session

    win = LoginWindow(prefill_key=saved_key)
    if win.exec() != QDialog.Accepted or win.session is None:
        raise SystemExit(0)
    return win.session


if __name__ == "__main__":
    # Standalone preview of the login screen
    app = QApplication([])
    w = LoginWindow()
    w.show()
    app.exec()
