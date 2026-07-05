"""PyQt UI for the local Roblox copier."""
from __future__ import annotations

import os
import sys
from typing import Optional

try:  # PyQt6 preferred for the standalone copier.
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import (
        QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
        QMessageBox, QPushButton, QPlainTextEdit, QVBoxLayout, QWidget,
    )
    PYQT6 = True
except Exception:  # Dev fallback: the existing loader currently uses PyQt5.
    from PyQt5.QtCore import Qt, QTimer
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import (
        QApplication, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
        QMessageBox, QPushButton, QPlainTextEdit, QVBoxLayout, QWidget,
    )
    PYQT6 = False

from server import LocalCopierServer, PORT

STYLE = """
QWidget { background: #0B0D10; color: #F8FAFC; font-family: Consolas, 'JetBrains Mono', monospace; font-size: 10pt; }
QLabel#Title { font-size: 16pt; font-weight: 700; color: #F8FAFC; }
QLabel#Muted { color: #94A3B8; }
QLineEdit, QPlainTextEdit { background: #111E2E; border: 1px solid #2A3340; color: #F8FAFC; padding: 7px; }
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #3B82F6; }
QPushButton { background: #1E3A5F; border: 1px solid #2A3340; color: #F8FAFC; padding: 8px 14px; font-weight: 700; }
QPushButton:hover { background: #3B82F6; border-color: #60A5FA; }
QPushButton:disabled { background: #151A21; color: #64748B; border-color: #222A35; }
QCheckBox { color: #CBD5E1; }
"""


class RobloxCopierWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rivvak Roblox Copier")
        self.resize(760, 560)
        self.server: Optional[LocalCopierServer] = None
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._sync_state)
        self._timer.start(800)

    def _align_left(self):
        return Qt.AlignmentFlag.AlignLeft if PYQT6 else Qt.AlignLeft

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(22, 20, 22, 20)
        lay.setSpacing(12)

        title = QLabel("Roblox Copier")
        title.setObjectName("Title")
        lay.addWidget(title)

        disclaimer = QLabel("For use only with games you own or have explicit permission to copy. Your .ROBLOSECURITY cookie is used locally and only sent to roblox.com.")
        disclaimer.setObjectName("Muted")
        disclaimer.setWordWrap(True)
        lay.addWidget(disclaimer)

        lay.addWidget(QLabel(".ROBLOSECURITY cookie"))
        self.cookie_input = QLineEdit()
        self.cookie_input.setEchoMode(QLineEdit.EchoMode.Password if PYQT6 else QLineEdit.Password)
        self.cookie_input.setPlaceholderText("Paste cookie value here (not stored)")
        lay.addWidget(self.cookie_input)

        row = QHBoxLayout()
        self.group_input = QLineEdit()
        self.group_input.setPlaceholderText("Optional group ID")
        row.addWidget(self.group_input, 1)
        self.show_cookie = QCheckBox("Show cookie")
        self.show_cookie.toggled.connect(self._toggle_cookie)
        row.addWidget(self.show_cookie)
        lay.addLayout(row)

        controls = QHBoxLayout()
        self.start_btn = QPushButton("Start server")
        self.start_btn.clicked.connect(self._start_server)
        controls.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop_server)
        self.stop_btn.setEnabled(False)
        controls.addWidget(self.stop_btn)
        self.copy_lua_btn = QPushButton("Open LuaScript.lua folder")
        self.copy_lua_btn.clicked.connect(self._open_lua_folder)
        controls.addWidget(self.copy_lua_btn)
        controls.addStretch(1)
        lay.addLayout(controls)

        self.status = QLabel(f"Stopped. Start this server, then run roblox_copier/LuaScript.lua in Roblox Studio. Port: {PORT}")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        lay.addWidget(self.log, 1)
        self.setStyleSheet(STYLE)

    def _toggle_cookie(self, checked: bool) -> None:
        if PYQT6:
            self.cookie_input.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        else:
            self.cookie_input.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def _append_log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def _parse_group_id(self) -> Optional[int]:
        text = self.group_input.text().strip()
        if not text:
            return None
        try:
            value = int(text)
            return value if value > 0 else None
        except ValueError:
            QMessageBox.warning(self, "Invalid group ID", "Group ID must be a number or blank.")
            return None

    def _start_server(self) -> None:
        cookie = self.cookie_input.text().strip()
        if not cookie:
            QMessageBox.warning(self, "Cookie required", "Paste your .ROBLOSECURITY cookie before starting.")
            return
        group_id = self._parse_group_id()
        if self.server:
            self.server.configure(cookie, group_id)
            self._append_log("Updated server cookie/group settings.")
            return
        try:
            self.server = LocalCopierServer(cookie=cookie, group_id=group_id, log=self._append_log)
            self.server.start()
        except OSError as exc:
            self.server = None
            QMessageBox.critical(self, "Server failed", f"Could not start 127.0.0.1:{PORT}: {exc}")
            return
        self.start_btn.setText("Update settings")
        self.stop_btn.setEnabled(True)
        self.status.setText(f"Running on http://127.0.0.1:{PORT}. Run LuaScript.lua in Roblox Studio.")

    def _stop_server(self) -> None:
        if self.server:
            self.server.stop()
            self.server = None
        self.start_btn.setText("Start server")
        self.stop_btn.setEnabled(False)
        self.status.setText("Stopped.")

    def _sync_state(self) -> None:
        if self.server and self.server.state.shutdown_requested:
            self.server.stop()
            self.server = None
            self.start_btn.setText("Start server")
            self.stop_btn.setEnabled(False)
            self.status.setText("Finished. Server stopped after Studio received the ID map.")

    def _open_lua_folder(self) -> None:
        path = os.environ.get("ROBLOX_COPIER_RESOURCE_DIR") or os.path.dirname(os.path.abspath(__file__))
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                import subprocess
                subprocess.Popen(["open", path])
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            QMessageBox.information(self, "LuaScript.lua", os.path.join(path, "LuaScript.lua") + f"\n\n{exc}")

    def closeEvent(self, event):
        self._stop_server()
        event.accept()


def run() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = RobloxCopierWindow()
    win.show()
    return app.exec() if PYQT6 else app.exec_()
