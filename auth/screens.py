"""
Pre-launch screens:
  1. TOS screen  — shown once per TOS version, blocks until accepted
  2. License screen — shown until a valid session exists

Both are modal Tk windows that block the caller until resolved.
Usage:
    from auth.screens import run_preflight
    run_preflight()   # raises SystemExit if user declines / can't activate
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import json
import os
import webbrowser
from pathlib import Path
from datetime import datetime

# ── Colour palette (matches steamguard.py) ────────────────────────────────────
BG_DARK  = "#0f1923"
BG_PANEL = "#16202c"
BG_CARD  = "#1e2d3d"
BORDER   = "#253a4e"
TEXT_MAIN= "#e2e8f0"
TEXT_DIM = "#64748b"
ACCENT   = "#2563eb"
GREEN    = "#22c55e"
RED      = "#ef4444"
YELLOW   = "#f59e0b"
F_BODY   = ("Segoe UI", 10)
F_SMALL  = ("Segoe UI", 9)

# ── Config / persistence ──────────────────────────────────────────────────────
_APPDATA     = Path(os.environ.get("APPDATA", "")) / "SteamGuard"
_CONFIG_FILE = _APPDATA / "config.json"

TOS_VERSION  = "1.0"   # bump this to force re-acceptance

TOS_TEXT = """STEAMGUARD — END USER LICENSE AGREEMENT & TERMS OF SERVICE
Version 1.0 — Effective upon acceptance

PLEASE READ THESE TERMS CAREFULLY BEFORE USING STEAMGUARD.
BY CLICKING "I ACCEPT", YOU AGREE TO BE BOUND BY THESE TERMS.

────────────────────────────────────────────────────────────────

1. LICENSE GRANT
   Subject to your compliance with these Terms, you are granted a
   limited, personal, non-exclusive, non-transferable, revocable
   license to install and use SteamGuard ("Software") on one (1)
   personal computer that you own or control.

2. RESTRICTIONS
   You may NOT:
   a) Copy, distribute, sell, or sublicense the Software or your
      license key to any third party.
   b) Reverse engineer, decompile, disassemble, or attempt to derive
      the source code of the Software.
   c) Modify, adapt, translate, or create derivative works based on
      the Software.
   d) Remove, alter, or obscure any copyright or proprietary notices.
   e) Use the Software for any unlawful purpose or in violation of
      any applicable law or regulation.
   f) Share, post, or publicly disclose your license key.

3. DISCORD MEMBERSHIP REQUIREMENT
   Your license is contingent on maintaining active membership in
   the designated Discord server and holding the required role.
   Your access will be automatically suspended if you:
   a) Leave the Discord server.
   b) Lose the required membership role.
   c) Are removed or banned from the Discord server.
   No refunds are issued in such circumstances.

4. HARDWARE BINDING
   Your license key will be bound to the hardware of the first
   machine on which you activate it. Using the Software on a
   different machine requires contacting support. The Software
   collects a hardware identifier (a one-way hash of system
   components) for this purpose. Raw hardware data is never
   transmitted or stored.

5. DISCLAIMER OF WARRANTIES
   THE SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND,
   EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
   OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND
   NON-INFRINGEMENT. WE DO NOT WARRANT THAT THE SOFTWARE WILL
   BE ERROR-FREE, UNINTERRUPTED, OR FREE OF SECURITY VULNERABILITIES.

6. LIMITATION OF LIABILITY
   IN NO EVENT SHALL THE DEVELOPERS OR CONTRIBUTORS BE LIABLE FOR
   ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR
   PUNITIVE DAMAGES, INCLUDING BUT NOT LIMITED TO LOSS OF DATA,
   LOSS OF PROFITS, LOSS OF GOODWILL, OR GAME ACCOUNT SUSPENSION,
   ARISING OUT OF OR IN CONNECTION WITH YOUR USE OF THE SOFTWARE,
   EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES. OUR TOTAL
   LIABILITY SHALL NOT EXCEED THE AMOUNT YOU PAID FOR THE SOFTWARE
   IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM.

7. STEAM & VALVE DISCLAIMER
   STEAMGUARD IS NOT AFFILIATED WITH, ENDORSED BY, OR SPONSORED BY
   VALVE CORPORATION OR STEAM. USE OF THIS SOFTWARE MAY VIOLATE
   VALVE'S STEAM SUBSCRIBER AGREEMENT AND/OR STEAM TERMS OF SERVICE.
   YOU ASSUME ALL RISK OF ACCOUNT SUSPENSION, BAN, OR OTHER
   CONSEQUENCES. WE ACCEPT NO LIABILITY FOR ANY SUCH OUTCOMES.

8. NO REFUNDS
   ALL PURCHASES ARE FINAL. WE DO NOT OFFER REFUNDS EXCEPT WHERE
   REQUIRED BY APPLICABLE LAW.

9. TERMINATION
   This license is effective until terminated. It terminates
   automatically if you breach any of these Terms. Upon termination
   you must cease all use of the Software and delete all copies.
   We may also terminate or suspend your license at any time for
   any reason without notice.

10. GOVERNING LAW
    These Terms shall be governed by and construed in accordance with
    applicable law. Any disputes shall be resolved in the appropriate
    courts of the applicable jurisdiction.

11. ENTIRE AGREEMENT
    These Terms constitute the entire agreement between you and us
    regarding the Software and supersede all prior agreements.

BY CLICKING "I ACCEPT" YOU CONFIRM:
  • You have read and understood these Terms.
  • You are at least 13 years of age.
  • You own or have the right to play any game you use with this Software.
  • You accept all risks described herein.
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


# ── TOS Window ────────────────────────────────────────────────────────────────

class TOSWindow(tk.Toplevel):
    """
    Modal TOS acceptance window.
    Sets self.accepted = True if user clicks Accept.
    """

    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("SteamGuard — Terms of Service")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        self.grab_set()    # modal
        self.accepted = False

        self._build()
        self.update_idletasks()
        w, h = 620, 580
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.protocol("WM_DELETE_WINDOW", self._decline)

    def _build(self):
        # Header
        hdr = tk.Frame(self, bg=BG_PANEL, height=50)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Terms of Service & License Agreement",
                 bg=BG_PANEL, fg=TEXT_MAIN, font=("Segoe UI", 12, "bold")
                 ).place(relx=0.5, rely=0.5, anchor="center")

        # Version badge
        tk.Label(hdr, text=f"v{TOS_VERSION}", bg=BG_PANEL, fg=TEXT_DIM,
                 font=F_SMALL).place(relx=1.0, x=-12, rely=0.5, anchor="e")

        # Instruction
        tk.Label(self,
                 text="Scroll to the bottom and check the box to continue.",
                 bg=BG_DARK, fg=YELLOW, font=F_SMALL
                 ).pack(pady=(8, 2))

        # Text area
        self._text = scrolledtext.ScrolledText(
            self, bg=BG_CARD, fg=TEXT_DIM, font=("Courier New", 9),
            relief="flat", bd=0, wrap="word",
            state="normal", height=22)
        self._text.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self._text.insert("end", TOS_TEXT)
        self._text.config(state="disabled")
        self._text.bind("<KeyRelease>", self._check_scroll)
        self._text.bind("<MouseWheel>",  self._check_scroll)
        self._text.bind("<ButtonRelease>", self._check_scroll)

        # Scroll progress bar
        self._progress_var = tk.DoubleVar(value=0.0)
        prog_frame = tk.Frame(self, bg=BG_DARK)
        prog_frame.pack(fill="x", padx=12, pady=(0, 4))
        self._prog_bar_bg = tk.Frame(prog_frame, bg=BORDER, height=4)
        self._prog_bar_bg.pack(fill="x")
        self._prog_bar_fg = tk.Frame(self._prog_bar_bg, bg=ACCENT, height=4, width=0)
        self._prog_bar_fg.place(x=0, y=0, relheight=1.0)

        # Checkbox
        self._accepted_var = tk.BooleanVar(value=False)
        self._chk = tk.Checkbutton(
            self,
            text="I have read, understood, and agree to the Terms of Service",
            variable=self._accepted_var,
            bg=BG_DARK, fg=TEXT_DIM,
            font=F_SMALL,
            selectcolor=BG_CARD, activebackground=BG_DARK,
            activeforeground=TEXT_MAIN,
            highlightthickness=0,
            state="disabled",
            command=self._on_checkbox)
        self._chk.pack(pady=(2, 4))

        # Buttons
        btn_row = tk.Frame(self, bg=BG_DARK)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))

        self._accept_btn = tk.Button(
            btn_row, text="I Accept",
            bg=TEXT_DIM, fg="white",
            font=("Segoe UI", 10, "bold"),
            relief="flat", bd=0, cursor="hand2",
            state="disabled",
            command=self._accept)
        self._accept_btn.pack(side="right", ipady=8, ipadx=24)

        tk.Button(btn_row, text="Decline & Exit",
                  bg=BG_CARD, fg=RED,
                  font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                  command=self._decline
                  ).pack(side="right", ipady=8, ipadx=16, padx=(0, 8))

    def _check_scroll(self, event=None):
        """Enable checkbox once user has scrolled near the bottom."""
        self.after(50, self._do_check_scroll)

    def _do_check_scroll(self):
        try:
            _, end = self._text.yview()
            # Update progress bar
            total_w = self._prog_bar_bg.winfo_width()
            self._prog_bar_fg.place_configure(width=int(total_w * end))
            if end >= 0.97:
                self._chk.config(state="normal", fg=TEXT_MAIN)
        except Exception:
            pass

    def _on_checkbox(self):
        if self._accepted_var.get():
            self._accept_btn.config(state="normal", bg=ACCENT,
                                    activebackground="#1c6cc4")
        else:
            self._accept_btn.config(state="disabled", bg=TEXT_DIM)

    def _accept(self):
        cfg = _load_config()
        cfg["tos_version"]      = TOS_VERSION
        cfg["tos_accepted_at"]  = datetime.now().isoformat()
        _save_config(cfg)
        self.accepted = True
        self.destroy()

    def _decline(self):
        self.destroy()


# ── License / Activation Window ──────────────────────────────────────────────

class LicenseWindow(tk.Toplevel):
    """
    License activation window.
    Shows key input + Discord ID input.
    On success sets self.session to the auth result dict and destroys.
    On failure (close button) sets self.session = None.
    """

    def __init__(self, parent: tk.Tk, prefill_key: str = "", error: str = ""):
        super().__init__(parent)
        self.title("SteamGuard — Activate License")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        self.grab_set()
        self.session = None
        self._busy   = False
        self._prefill_error = error
        self._timeout_id = None

        self._build(prefill_key)
        self.update_idletasks()
        w, h = 500, 420
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self, prefill_key: str):
        # Header
        hdr = tk.Frame(self, bg=BG_PANEL, height=50)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Activate SteamGuard",
                 bg=BG_PANEL, fg=TEXT_MAIN,
                 font=("Segoe UI", 12, "bold")
                 ).place(relx=0.5, rely=0.5, anchor="center")

        # Shield icon area
        shield_frame = tk.Frame(self, bg=BG_DARK)
        shield_frame.pack(pady=(16, 4))
        cv = tk.Canvas(shield_frame, width=56, height=62, bg=BG_DARK,
                       highlightthickness=0)
        cv.pack()
        # Draw shield polygon
        cx, cy = 28, 31
        pts = [cx, cy-28, cx+22, cy-18, cx+22, cy+4, cx, cy+28,
               cx-22, cy+4, cx-22, cy-18]
        cv.create_polygon(*pts, fill="#1a3a5c", outline=ACCENT, width=2)
        cv.create_text(cx, cy+2, text="S", fill=ACCENT,
                       font=("Segoe UI", 18, "bold"))

        tk.Label(self, text="Enter your license key and Discord User ID",
                 bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL).pack()

        # Form
        form = tk.Frame(self, bg=BG_DARK)
        form.pack(fill="x", padx=32, pady=(16, 0))

        tk.Label(form, text="License Key", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self._key_var = tk.StringVar(value=prefill_key)
        key_entry = tk.Entry(form, textvariable=self._key_var,
                             bg=BG_CARD, fg=TEXT_MAIN,
                             insertbackground=TEXT_MAIN,
                             font=("Courier New", 11),
                             relief="flat", bd=6)
        key_entry.pack(fill="x", pady=(2, 10))
        key_entry.bind("<KeyRelease>", lambda e: self._auto_format_key())

        tk.Label(form, text="Discord User ID", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self._discord_var = tk.StringVar()
        tk.Entry(form, textvariable=self._discord_var,
                 bg=BG_CARD, fg=TEXT_MAIN,
                 insertbackground=TEXT_MAIN,
                 font=F_BODY,
                 relief="flat", bd=6).pack(fill="x", pady=(2, 4))

        # Help: how to find Discord ID
        def _open_help():
            webbrowser.open(
                "https://support.discord.com/hc/en-us/articles/"
                "206346498-Where-can-I-find-my-User-Server-Message-ID-")
        tk.Button(form, text="How do I find my Discord User ID?",
                  bg=BG_DARK, fg=ACCENT,
                  font=("Segoe UI", 8), relief="flat", bd=0,
                  cursor="hand2", command=_open_help
                  ).pack(anchor="w", pady=(0, 8))

        # Status label
        self._status_lbl = tk.Label(
            self, text="", bg=BG_DARK, fg=TEXT_DIM,
            font=F_SMALL, wraplength=440)
        self._status_lbl.pack(pady=(4, 0))

        if self._prefill_error:
            self._set_status(f"✗ {self._prefill_error}", RED)

        # Activate button
        self._btn = tk.Button(
            self, text="Activate",
            bg=ACCENT, fg="white",
            font=("Segoe UI", 11, "bold"),
            relief="flat", bd=0, cursor="hand2",
            activebackground="#1c6cc4",
            command=self._on_activate)
        self._btn.pack(fill="x", padx=32, pady=(8, 0), ipady=10)

        # Join Discord link
        join_row = tk.Frame(self, bg=BG_DARK)
        join_row.pack(pady=(10, 0))
        tk.Label(join_row, text="Don't have a key? ",
                 bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL).pack(side="left")

        def _open_discord():
            # Replace this with your actual Discord invite link before release.
            webbrowser.open("https://discord.gg/REPLACE_YOUR_INVITE")
        tk.Button(join_row, text="Join our Discord",
                  bg=BG_DARK, fg=ACCENT, font=F_SMALL,
                  relief="flat", bd=0, cursor="hand2",
                  command=_open_discord).pack(side="left")

    def _auto_format_key(self):
        """Auto-insert dashes as the user types."""
        raw = self._key_var.get().upper().replace("-", "")
        if len(raw) > 4:
            raw = raw[:4] + "-" + raw[4:]
        if len(raw) > 13:
            raw = raw[:13] + "-" + raw[13:]
        if len(raw) > 22:
            raw = raw[:22] + "-" + raw[22:]
        if len(raw) > 31:
            raw = raw[:31] + "-" + raw[31:39]
        self._key_var.set(raw)

    def _on_activate(self):
        if self._busy:
            return
        key        = self._key_var.get().strip()
        discord_id = self._discord_var.get().strip()

        if len(key) < 10:
            self._set_status("Please enter your license key.", RED)
            return
        if not discord_id.isdigit():
            self._set_status("Discord User ID must be a number (17–19 digits).", RED)
            return

        self._busy = True
        self._btn.config(state="disabled", text="Activating…", bg=TEXT_DIM)
        self._set_status("Contacting license server…", TEXT_DIM)

        # Safety timeout: if the worker thread never returns, force-reset the UI
        self._timeout_id = self.after(20000, self._reset_on_timeout)

        def worker():
            from auth.client import activate
            from auth.cache  import save_session
            try:
                result = activate(key, discord_id)
            except Exception as e:
                # Should never happen because activate catches everything, but just in case
                from auth.client import AuthResult
                result = AuthResult(ok=False, error=f"Client crash: {e}")
            self.after(0, lambda: self._on_result(result, key, discord_id))

        threading.Thread(target=worker, daemon=True).start()

    def _reset_on_timeout(self):
        if self._busy:
            self._busy = False
            self._btn.config(state="normal", text="Activate", bg=ACCENT)
            self._set_status("Server did not respond in 20 seconds. Check your internet or try again.", RED)

    def _on_result(self, result, key: str, discord_id: str):
        from auth.client import AuthResult
        self._busy = False
        if self._timeout_id is not None:
            self.after_cancel(self._timeout_id)
            self._timeout_id = None
        self._btn.config(state="normal", text="Activate", bg=ACCENT)

        if result.ok:
            from auth.cache import save_session
            save_session(result.session_token, result.token_expires,
                         key, discord_id)
            # Persist key + discord ID for future verify calls
            cfg = _load_config()
            cfg["license_key"]      = key
            cfg["discord_user_id"]  = discord_id
            _save_config(cfg)

            self._set_status("✓ Activated successfully!", GREEN)
            self.session = {
                "session_token":   result.session_token,
                "token_expires":   result.token_expires,
                "key":             key,
                "discord_user_id": discord_id,
            }
            self.after(800, self.destroy)
        else:
            self._set_status(f"✗ {result.error}", RED)

    def _set_status(self, msg: str, color: str = TEXT_DIM):
        self._status_lbl.config(text=msg, fg=color)

    def _on_close(self):
        self.session = None
        self.destroy()


# ── Public entry point ────────────────────────────────────────────────────────

def run_preflight() -> dict:
    """
    Show TOS and/or License screen as needed.
    Returns a session dict on success.
    Raises SystemExit if the user cancels.

    Call this before creating the main SteamGuard window.
    """
    from auth.cache  import load_session, session_needs_refresh
    from auth.client import verify
    from auth.cache  import save_session

    root = tk.Tk()
    root.withdraw()   # hidden host window for modals

    # ── Step 1: TOS ───────────────────────────────────────────────────────────
    if not _tos_accepted():
        tos = TOSWindow(root)
        root.wait_window(tos)
        if not tos.accepted:
            root.destroy()
            raise SystemExit(0)

    # ── Step 2: Load saved credentials ───────────────────────────────────────
    cfg = _load_config()
    saved_key        = cfg.get("license_key", "")
    saved_discord_id = cfg.get("discord_user_id", "")

    # ── Step 3: Check cache ───────────────────────────────────────────────────
    cached = load_session()
    if cached and not session_needs_refresh():
        root.destroy()
        return cached   # still valid, no server call needed

    # ── Step 4: Re-verify in background if we have credentials ───────────────
    if saved_key and saved_discord_id:
        result = verify(saved_key, saved_discord_id)
        if result.ok:
            save_session(result.session_token, result.token_expires,
                         saved_key, saved_discord_id)
            root.destroy()
            return {
                "session_token":   result.session_token,
                "token_expires":   result.token_expires,
                "key":             saved_key,
                "discord_user_id": saved_discord_id,
            }
        # Verification failed (left Discord, revoked, paused, etc.)
        # Show the activation screen with the error pre-filled so the user sees it
        error_msg = result.error or "Verification failed"
        lic = LicenseWindow(root, prefill_key=saved_key, error=error_msg)
        root.wait_window(lic)

        if lic.session is None:
            root.destroy()
            raise SystemExit(0)

        root.destroy()
        return lic.session

    # ── Step 5: Show activation screen ───────────────────────────────────────
    lic = LicenseWindow(root, prefill_key=saved_key)
    root.wait_window(lic)

    if lic.session is None:
        root.destroy()
        raise SystemExit(0)

    root.destroy()
    return lic.session
