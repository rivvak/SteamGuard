"""Loader chat modal — QWebEngineView popup launched from a '?' button.

Public API (used from loader.py):

    from loader_ai.chat_panel import open_chat_modal
    open_chat_modal(parent=self, key=session.key, hwid=session.hwid,
                    server_url=auth_client._SERVER_URL)

The modal is a small HTML/JS chat widget hosted in QWebEngineView. It
POSTs {key, hwid, sig, question} to <server_url>/ai/ask and renders the
SSE stream inline. Nothing runs in the loader's own process — the widget
is a webview.

Gate the '?' button on the AI_ASK_ENABLED loader env var (or a version
probe against /health) so users on old server revisions don't see it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import quote

from PyQt5.QtCore import QUrl, Qt
from PyQt5.QtWebEngineWidgets import QWebEngineView
from PyQt5.QtWidgets import QDialog, QVBoxLayout

_UI_DIR = Path(__file__).resolve().parent / "chat_ui"


def _sig(secret: str, key: str, hwid: str) -> str:
    return hmac.new(secret.encode(), f"{key}:{hwid}".encode(), hashlib.sha256).hexdigest()


def _bootstrap_html(server_url: str, key: str, hwid: str, sig: str) -> str:
    """Load index.html and inject session parameters as a data-URL bootstrap."""
    html = (_UI_DIR / "index.html").read_text(encoding="utf-8")
    css = (_UI_DIR / "chat.css").read_text(encoding="utf-8")
    js = (_UI_DIR / "chat.js").read_text(encoding="utf-8")

    config = json.dumps(
        {
            "serverUrl": server_url.rstrip("/"),
            "key": key,
            "hwid": hwid,
            "sig": sig,
        }
    )
    return (
        html
        .replace("/* __STYLE__ */", css)
        .replace("/* __CONFIG__ */", f"window.__SG_AI_CONFIG__ = {config};")
        .replace("/* __SCRIPT__ */", js)
    )


def open_chat_modal(parent, key: str, hwid: str, server_url: str) -> None:
    """Show the chat modal as a QDialog. Non-blocking; parent keeps focus after close."""
    # HMAC secret must match the one used in auth/client.py (loader-side).
    hmac_secret = os.environ.get(
        "SG_HMAC_SECRET",
        # Same fallback constant as auth/client.py — keep in sync.
        "7e3b9ccf02a09ad3520ebc7ed3f00a48d5eff34ef081900ee9064dba2a74529e",
    )
    sig = _sig(hmac_secret, key, hwid)

    dlg = QDialog(parent)
    dlg.setWindowTitle("SteamGuard Assistant")
    dlg.setModal(False)
    dlg.resize(560, 640)
    dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowMaximizeButtonHint)

    view = QWebEngineView(dlg)
    view.setHtml(_bootstrap_html(server_url, key, hwid, sig), QUrl("about:blank"))

    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(view)

    dlg.show()
