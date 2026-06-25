"""
License server client.
Handles activate / verify calls with request signing and
certificate pinning (rejects MITM proxies).
"""

import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import json
import os
import webbrowser
import queue  # <--- Added for thread-safe UI communication
from pathlib import Path
from datetime import datetime

# ── Colour palette ────────────────────────────────────────────────────────────
BG_DARK  = "#0d1117"
BG_PANEL = "#1c2128"
BG_CARD  = "#21262d"
BORDER   = "#30363d"
TEXT_MAIN= "#e6edf3"
TEXT_DIM = "#8b949e"
ACCENT   = "#58a6ff"
GREEN    = "#3fb950"
RED      = "#f85149"
YELLOW   = "#d29922"
F_BODY   = ("Segoe UI", 10)
F_SMALL  = ("Segoe UI", 9)
F_MONO   = ("Consolas", 9)

# ── Config / persistence ──────────────────────────────────────────────────────
_APPDATA     = Path(os.environ.get("APPDATA", "")) / "SteamGuard"
_CONFIG_FILE = _APPDATA / "config.json"

TOS_VERSION  = "1.1"

# (TOS_TEXT remains the same as original)
TOS_TEXT = """STEAMGUARD — END USER LICENSE AGREEMENT & TERMS OF SERVICE
Version 1.1 — Effective upon acceptance

PLEASE READ THESE TERMS CAREFULLY BEFORE USING STEAMGUARD.
BY CLICKING "I ACCEPT", YOU AGREE TO BE BOUND BY THESE TERMS.

THIS SOFTWARE IS PROVIDED FOR EDUCATIONAL AND RESEARCH PURPOSES ONLY.

────────────────────────────────────────────────────────────────

1. LICENSE GRANT
   Subject to your compliance with these Terms, you are granted a
   limited, personal, non-exclusive, non-transferable, revocable
   license to install and use SteamGuard ("Software") on one (1)
   personal computer that you own or control, strictly for
   educational, research, and personal learning purposes.

2. EDUCATIONAL USE ONLY
   SteamGuard is intended solely as an educational tool to demonstrate
   and study two-factor authentication, software licensing, hardware
   identification, and related concepts. You may not use the Software
   to violate any third-party terms of service, circumvent security
   measures, or engage in any activity that is unlawful or prohibited.
   Any use of the Software for commercial, fraudulent, or malicious
   purposes is strictly prohibited.

3. RESTRICTIONS
   You may NOT:
   a) Copy, distribute, sell, rent, lease, sublicense, or transfer
      the Software or your license key to any third party.
   b) Reverse engineer, decompile, disassemble, or attempt to derive
      the source code of the Software.
   c) Modify, adapt, translate, or create derivative works based on
      the Software.
   d) Remove, alter, or obscure any copyright or proprietary notices.
   e) Use the Software for any unlawful purpose, fraud, or in violation
      of any applicable law, regulation, or third-party agreement.
   f) Share, post, or publicly disclose your license key.
   g) Use the Software to interfere with, disrupt, or gain unauthorized
      access to any service, account, system, or network.

4. DISCORD MEMBERSHIP REQUIREMENT
   Your license is contingent on maintaining active membership in
   the designated Discord server and holding the required role.
   Your access will be automatically suspended if you:
   a) Leave the Discord server.
   b) Lose the required membership role.
   c) Are removed or banned from the Discord server.
   No refunds are issued in such circumstances.

5. HARDWARE BINDING
   Your license key will be bound to the hardware of the first
   machine on which you activate it. Using the Software on a
   different machine requires contacting support. The Software
   collects a hardware identifier (a one-way hash of system
   components) for this purpose. Raw hardware data is never
   transmitted or stored.

6. DISCLAIMER OF WARRANTIES
   THE SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND,
   EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
   OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE,
   NON-INFRINGEMENT, AND FITNESS FOR ANY SPECIFIC PURPOSE. WE DO
   NOT WARRANT THAT THE SOFTWARE WILL BE ERROR-FREE, UNINTERRUPTED,
   SECURE, FREE OF VIRUSES, OR FREE OF SECURITY VULNERABILITIES.
   YOU USE THE SOFTWARE ENTIRELY AT YOUR OWN RISK.

7. LIMITATION OF LIABILITY
   TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT
   SHALL THE DEVELOPERS, OWNERS, OPERATORS, CONTRIBUTORS, OR
   AFFILIATES OF STEAMGUARD BE LIABLE FOR ANY DIRECT, INDIRECT,
   INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, PUNITIVE, OR
   ANY OTHER DAMAGES, INCLUDING BUT NOT LIMITED TO LOSS OF DATA,
   LOSS OF PROFITS, LOSS OF GOODWILL, BUSINESS INTERRUPTION,
   PERSONAL INJURY, PROPERTY DAMAGE, OR GAME ACCOUNT SUSPENSION,
   ARISING OUT OF OR IN CONNECTION WITH YOUR USE OF OR INABILITY TO
   USE THE SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.
   OUR TOTAL LIABILITY SHALL NOT EXCEED THE AMOUNT YOU PAID FOR THE
   SOFTWARE IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM, IF ANY.

8. INDEMNIFICATION
   You agree to defend, indemnify, and hold harmless the developers,
   owners, operators, contributors, and affiliates of SteamGuard from
   and against any and all claims, damages, obligations, losses,
   liabilities, costs, debts, and expenses (including attorneys' fees)
   arising out of or related to your use of the Software, your violation
   of these Terms, or your violation of any third-party right, including
   without limitation any copyright, property, or privacy right.

9. STEAM & VALVE DISCLAIMER
   STEAMGUARD IS NOT AFFILIATED WITH, ENDORSED BY, OR SPONSORED BY
   VALVE CORPORATION OR STEAM. USE OF THIS SOFTWARE MAY VIOLATE
   VALVE'S STEAM SUBSCRIBER AGREEMENT AND/OR STEAM TERMS OF SERVICE.
   YOU ASSUME ALL RISK OF ACCOUNT SUSPENSION, BAN, OR OTHER
   CONSEQUENCES. WE ACCEPT NO LIABILITY FOR ANY SUCH OUTCOMES.

10. NO REFUNDS
    ALL PURCHASES ARE FINAL. WE DO NOT OFFER REFUNDS EXCEPT WHERE
    REQUIRED BY APPLICABLE LAW.

11. TERMINATION
    This license is effective until terminated. It terminates
    automatically if you breach any of these Terms. Upon termination
    you must cease all use of the Software and delete all copies.
    We may also terminate or suspend your license at any time for
    any reason without notice.

12. GOVERNING LAW & DISPUTE RESOLUTION
    These Terms shall be governed by and construed in accordance with
    applicable law. Any dispute, controversy, or claim arising out of or
    relating to these Terms or the Software shall be resolved through
    binding arbitration on an individual basis, except that either party
    may seek injunctive relief in a court of competent jurisdiction.
    You waive any right to participate in class actions, class
    arbitrations, or representative actions. Any arbitration shall be
    conducted in the jurisdiction chosen by the Software provider.

13. LEGAL NOTICE
    This Software and its licensing system are provided for educational
    demonstration. Nothing in these Terms creates a partnership,
    agency, joint venture, or employment relationship. We reserve the
    right to modify these Terms at any time. Continued use of the
    Software after changes constitutes acceptance of the revised Terms.

14. ENTIRE AGREEMENT
    These Terms constitute the entire agreement between you and us
    regarding the Software and supersede all prior agreements,
    understandings, representations, and warranties.

BY CLICKING "I ACCEPT" YOU CONFIRM:
  • You have read, understood, and agree to all Terms above.
  • You are at least 18 years of age or have parental consent.
  • You will use the Software only for educational and lawful purposes.
  • You own or have the right to play any game you use with this Software.
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


# ── DPAPI helpers ─────────────────────────────────────────────────────────────

def _dpapi_protect(data: bytes) -> bytes:
    """Encrypt bytes with Windows DPAPI (current-user scope). Falls back to plaintext on failure."""
    try:
        import win32crypt
        protected = win32crypt.CryptProtectData(
            data, "SteamGuard-Session", None, None, None, 0)
        return protected
    except Exception:
        return data  # graceful fallback for dev mode / missing pywin32

def _dpapi_unprotect(blob: bytes) -> bytes:
    """Decrypt DPAPI-protected bytes. Falls back to returning blob as-is on failure."""
    try:
        import win32crypt
        _, decrypted = win32crypt.CryptUnprotectData(
            blob, None, None, None, 0)
        return decrypted
    except Exception:
        return blob  # graceful fallback

def _load_dpapi_token() -> str:
    """Try to load the DPAPI-protected session token. Returns empty string on failure."""
    try:
        import base64 as _b64
        cfg = _load_config()
        blob_b64 = cfg.get("session_token_dpapi", "")
        if not blob_b64:
            return ""
        blob = _b64.b64decode(blob_b64)
        return _dpapi_unprotect(blob).decode("utf-8")
    except Exception:
        return ""


# ── Tooltip ───────────────────────────────────────────────────────────────────

class Tooltip:
    def __init__(self, widget, text, delay=600):
        self._widget = widget
        self._text = text
        self._delay = delay
        self._tip_win = None
        self._after_id = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._cancel)
        widget.bind("<ButtonPress>", self._cancel)

    def _schedule(self, event=None):
        self._cancel()
        self._after_id = self._widget.after(self._delay, self._show)

    def _cancel(self, event=None):
        if self._after_id:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip_win:
            self._tip_win.destroy()
            self._tip_win = None

    def _show(self):
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        self._tip_win = tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self._text, bg="#1c2128", fg="#e6edf3",
                 font=("Segoe UI", 8), relief="flat", bd=0,
                 padx=8, pady=4).pack()
        tw.after(3000, self._cancel)


# ── TOS Window ────────────────────────────────────────────────────────────────

class TOSWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk):
        super().__init__(parent)
        self.title("SteamGuard — Terms of Service")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        self.grab_set()
        self.accepted = False
        self._build()
        self.update_idletasks()
        w, h = 620, 580
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.protocol("WM_DELETE_WINDOW", self._decline)

    def _build(self):
        # ── Improved header: 56px with shield icon ──
        hdr = tk.Frame(self, bg=BG_PANEL, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # Small shield canvas at x=14, y=11 (30x34)
        shield_cv = tk.Canvas(hdr, width=30, height=34, bg=BG_PANEL, highlightthickness=0)
        shield_cv.place(x=14, y=11)
        # Shield polygon fitted to 30x34
        pts = [15, 0,  30, 6,  30, 20,  15, 34,  0, 20,  0, 6]
        shield_cv.create_polygon(*pts, fill=ACCENT, outline="")
        shield_cv.create_text(15, 17, text="S", fill="white", font=("Segoe UI", 11, "bold"))

        # Title and subtitle
        tk.Label(hdr, text="Terms of Service", bg=BG_PANEL, fg=TEXT_MAIN,
                 font=("Segoe UI", 12, "bold")).place(x=56, y=10)
        tk.Label(hdr, text="Read carefully before using SteamGuard", bg=BG_PANEL,
                 fg=TEXT_DIM, font=F_SMALL).place(x=56, y=30)

        tk.Label(hdr, text=f"v{TOS_VERSION}", bg=BG_PANEL, fg=TEXT_DIM,
                 font=F_SMALL).place(relx=1.0, x=-12, rely=0.5, anchor="e")

        tk.Label(self, text="Scroll to the bottom and check the box to continue.",
                 bg=BG_DARK, fg=YELLOW, font=F_SMALL).pack(pady=(8, 2))

        # TOS scrolled text: bg=BG_CARD, fg=TEXT_DIM, font=Consolas 9
        self._text = scrolledtext.ScrolledText(
            self, bg=BG_CARD, fg=TEXT_DIM, font=("Consolas", 9),
            relief="flat", bd=0, wrap="word", state="normal", height=22)
        self._text.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self._text.insert("end", TOS_TEXT)
        self._text.config(state="disabled")
        self._text.bind("<KeyRelease>", self._check_scroll)
        self._text.bind("<MouseWheel>",  self._check_scroll)
        self._text.bind("<ButtonRelease>", self._check_scroll)

        # Progress bar: 6px tall, ACCENT fill, BG_CARD background
        self._progress_var = tk.DoubleVar(value=0.0)
        prog_frame = tk.Frame(self, bg=BG_DARK)
        prog_frame.pack(fill="x", padx=12, pady=(0, 4))
        self._prog_bar_bg = tk.Frame(prog_frame, bg=BG_CARD, height=6)
        self._prog_bar_bg.pack(fill="x")
        self._prog_bar_fg = tk.Frame(self._prog_bar_bg, bg=ACCENT, height=6, width=0)
        self._prog_bar_fg.place(x=0, y=0, relheight=1.0)

        # Checkbox: disabled until scrolled; enabled state uses TEXT_MAIN + ACCENT selectcolor
        self._accepted_var = tk.BooleanVar(value=False)
        self._chk = tk.Checkbutton(
            self, text="I have read, understood, and agree to the Terms of Service",
            variable=self._accepted_var, bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL,
            selectcolor=BG_CARD, activebackground=BG_DARK, activeforeground=TEXT_MAIN,
            highlightthickness=0, state="disabled", command=self._on_checkbox)
        self._chk.pack(pady=(2, 4))

        btn_row = tk.Frame(self, bg=BG_DARK)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))

        # Accept button: disabled uses TEXT_DIM; when enabled uses GREEN
        self._accept_btn = tk.Button(
            btn_row, text="I Accept", bg=TEXT_DIM, fg="white",
            font=("Segoe UI", 10, "bold"), relief="flat", bd=0, cursor="hand2",
            state="disabled", command=self._accept)
        self._accept_btn.pack(side="right", ipady=8, ipadx=24)

        # Decline button: bg=BG_CARD, fg=RED
        tk.Button(btn_row, text="Decline & Exit", bg=BG_CARD, fg=RED,
                  font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                  command=self._decline).pack(side="right", ipady=8, ipadx=16, padx=(0, 8))

    def _check_scroll(self, event=None):
        self.after(50, self._do_check_scroll)

    def _do_check_scroll(self):
        try:
            _, end = self._text.yview()
            total_w = self._prog_bar_bg.winfo_width()
            self._prog_bar_fg.place_configure(width=int(total_w * end))
            if end >= 0.97:
                # When enabled: TEXT_MAIN fg with ACCENT selectcolor
                self._chk.config(state="normal", fg=TEXT_MAIN, selectcolor=ACCENT)
        except Exception:
            pass

    def _on_checkbox(self):
        if self._accepted_var.get():
            # Accept button becomes GREEN when checkbox is ticked
            self._accept_btn.config(state="normal", bg=GREEN, activebackground="#2ea043")
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


# ── License / Activation Window ───────────────────────────────────────────────

class LicenseWindow(tk.Toplevel):
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
        self._spinner_id = None
        self._spinner_state = 0

        # Safe thread-communication queue
        self._queue = queue.Queue()

        self._build(prefill_key)
        self.update_idletasks()
        w, h = 500, 500
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            _ico = Path(__file__).parent.parent / "icon.ico"
            _png = Path(__file__).parent.parent / "icon.png"
            if _ico.exists():
                self.iconbitmap(str(_ico))
            elif _png.exists():
                _img = tk.PhotoImage(file=str(_png))
                self.iconphoto(True, _img)
        except Exception:
            pass

    def _build(self, prefill_key: str):
        # ── Improved header: 56px with shield icon ──
        hdr = tk.Frame(self, bg=BG_PANEL, height=56)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        # Small shield canvas on the left
        shield_cv = tk.Canvas(hdr, width=30, height=34, bg=BG_PANEL, highlightthickness=0)
        shield_cv.place(x=14, y=11)
        pts = [15, 0,  30, 6,  30, 20,  15, 34,  0, 20,  0, 6]
        shield_cv.create_polygon(*pts, fill=ACCENT, outline="")
        shield_cv.create_text(15, 17, text="S", fill="white", font=("Segoe UI", 11, "bold"))

        # Title centered
        tk.Label(hdr, text="Activate SteamGuard", bg=BG_PANEL, fg=TEXT_MAIN,
                 font=("Segoe UI", 12, "bold")).place(relx=0.5, rely=0.35, anchor="center")

        # Subtitle
        tk.Label(self, text="Enter your license key and Discord User ID",
                 bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL).pack(pady=(14, 0))

        # Thin separator line after subtitle
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=24, pady=(8, 0))

        form = tk.Frame(self, bg=BG_DARK)
        form.pack(fill="x", padx=32, pady=(16, 0))

        # ── License Key label + entry + Paste button inline ──
        tk.Label(form, text="License Key", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        self._key_var = tk.StringVar(value=prefill_key)
        key_row = tk.Frame(form, bg=BG_DARK)
        key_row.pack(fill="x", pady=(2, 10))

        key_entry = tk.Entry(key_row, textvariable=self._key_var, bg=BG_CARD, fg=TEXT_MAIN,
                             insertbackground=TEXT_MAIN, font=("Consolas", 11), relief="flat", bd=6)
        key_entry.pack(side="left", fill="x", expand=True)
        key_entry.bind("<KeyRelease>", lambda e: self._auto_format_key())
        Tooltip(key_entry, "Your license key in XXXX-XXXX-XXXX-XXXX format")

        def _paste_key():
            try:
                self._key_var.set(self.clipboard_get())
                self._auto_format_key()
            except Exception:
                pass

        paste_btn = tk.Button(key_row, text="Paste", bg=BG_CARD, fg=ACCENT, font=F_SMALL,
                              relief="flat", bd=0, cursor="hand2", padx=8, command=_paste_key)
        paste_btn.pack(side="left", padx=(4, 0), ipady=4)

        # ── Discord User ID label + entry + help icon inline ──
        tk.Label(form, text="Discord User ID", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        discord_row = tk.Frame(form, bg=BG_DARK)
        discord_row.pack(fill="x", pady=(2, 4))

        self._discord_var = tk.StringVar()
        discord_entry = tk.Entry(discord_row, textvariable=self._discord_var, bg=BG_CARD,
                                 fg=TEXT_MAIN, insertbackground=TEXT_MAIN, font=F_BODY,
                                 relief="flat", bd=6)
        discord_entry.pack(side="left", fill="x", expand=True)
        Tooltip(discord_entry, "Your 17-19 digit Discord User ID (not username)")

        def _open_help():
            webbrowser.open("https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID-")

        help_btn = tk.Button(discord_row, text="?", bg=BG_CARD, fg=ACCENT, font=F_SMALL,
                             relief="flat", bd=0, cursor="hand2", padx=6, command=_open_help)
        help_btn.pack(side="left", padx=(4, 0), ipady=4)
        Tooltip(help_btn, "Open Discord help page to find your User ID")

        tk.Button(form, text="How do I find my Discord User ID?", bg=BG_DARK, fg=ACCENT,
                  font=("Segoe UI", 8), relief="flat", bd=0, cursor="hand2",
                  command=_open_help).pack(anchor="w", pady=(0, 8))

        # ── Status label with colored left-border bar effect ──
        self._status_frame = tk.Frame(self, bg=BG_DARK)
        self._status_frame.pack(fill="x", padx=32, pady=(4, 0))

        # Left border indicator (3px, hidden initially)
        self._status_bar = tk.Frame(self._status_frame, bg=BG_DARK, width=3)
        self._status_bar.pack(side="left", fill="y")

        self._status_lbl = tk.Label(self._status_frame, text="", bg=BG_DARK, fg=TEXT_DIM,
                                    font=F_SMALL, wraplength=420, justify="left", padx=6)
        self._status_lbl.pack(side="left", fill="x", expand=True)

        if self._prefill_error:
            self._set_status(f"✗ {self._prefill_error}", RED)

        # ── Activate button with hover effects ──
        self._btn = tk.Button(
            self, text="Activate", bg=ACCENT, fg="white", font=("Segoe UI", 11, "bold"),
            relief="flat", bd=0, cursor="hand2", activebackground="#388bfd",
            command=self._on_activate)
        self._btn.pack(fill="x", padx=32, pady=(8, 0), ipady=10)
        Tooltip(self._btn, "Submit key and Discord ID to activate your license")

        # Hover effects on Activate button
        self._btn.bind("<Enter>", self._btn_hover_in)
        self._btn.bind("<Leave>", self._btn_hover_out)

        # ── Social links row (Discord + YouTube) ────────────────────────────
        tk.Label(self, text="Don't have a key?", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 8)).pack(pady=(12, 4))

        social_row = tk.Frame(self, bg=BG_DARK)
        social_row.pack(pady=(0, 8))

        DISCORD_COLOR = "#5865F2"
        YOUTUBE_COLOR = "#FF0000"
        DISCORD_URL   = "https://discord.gg/RTHM8YhpE"
        YOUTUBE_URL   = "https://www.youtube.com/@Rivvak"

        def _darken(hex_color):
            r = max(0, int(hex_color[1:3], 16) - 20)
            g = max(0, int(hex_color[3:5], 16) - 20)
            b = max(0, int(hex_color[5:7], 16) - 20)
            return f"#{r:02x}{g:02x}{b:02x}"

        def _make_social_btn(parent, color, label, url, draw_fn):
            frame = tk.Frame(parent, bg=color, cursor="hand2")
            frame.pack(side="left", padx=6)
            cv = tk.Canvas(frame, width=18, height=18, bg=color,
                           highlightthickness=0, cursor="hand2")
            cv.pack(side="left", padx=(8, 4), pady=6)
            draw_fn(cv, color)
            lbl = tk.Label(frame, text=label, bg=color, fg="white",
                           font=("Segoe UI", 9, "bold"), cursor="hand2")
            lbl.pack(side="left", padx=(0, 10), pady=6)
            def _click(e=None): webbrowser.open(url)
            def _hi(e):
                d = _darken(color)
                frame.config(bg=d); cv.config(bg=d); lbl.config(bg=d)
            def _lo(e):
                frame.config(bg=color); cv.config(bg=color); lbl.config(bg=color)
            for w in (frame, cv, lbl):
                w.bind("<Button-1>", _click)
                w.bind("<Enter>", _hi)
                w.bind("<Leave>", _lo)

        def _draw_discord(cv, bg):
            cv.create_oval(0, 0, 14, 12, fill="white", outline="")
            cv.create_rectangle(3, 6, 14, 12, fill="white", outline="")
            cv.create_rectangle(0, 3, 11, 12, fill="white", outline="")
            cv.create_polygon(2, 11, 0, 16, 7, 12, fill="white", outline="")
            cv.create_oval(3, 4, 6, 7, fill=bg, outline="")
            cv.create_oval(8, 4, 11, 7, fill=bg, outline="")

        def _draw_youtube(cv, bg):
            cv.create_rectangle(1, 4, 17, 14, fill="white", outline="")
            cv.create_oval(1, 4, 5, 8, fill="white", outline="")
            cv.create_oval(13, 4, 17, 8, fill="white", outline="")
            cv.create_oval(1, 10, 5, 14, fill="white", outline="")
            cv.create_oval(13, 10, 17, 14, fill="white", outline="")
            cv.create_polygon(7, 6, 7, 12, 13, 9, fill=bg, outline="")

        _make_social_btn(social_row, DISCORD_COLOR, "Join our Discord",
                         DISCORD_URL, _draw_discord)
        _make_social_btn(social_row, YOUTUBE_COLOR, "Rivvak on YouTube",
                         YOUTUBE_URL, _draw_youtube)

    def _btn_hover_in(self, event=None):
        if not self._busy:
            self._btn.config(bg="#388bfd")

    def _btn_hover_out(self, event=None):
        if not self._busy:
            self._btn.config(bg=ACCENT)

    def _auto_format_key(self):
        raw = self._key_var.get().upper().replace("-", "")
        if len(raw) > 4:   raw = raw[:4] + "-" + raw[4:]
        if len(raw) > 13:  raw = raw[:13] + "-" + raw[13:]
        if len(raw) > 22:  raw = raw[:22] + "-" + raw[22:]
        if len(raw) > 31:  raw = raw[:31] + "-" + raw[31:39]
        self._key_var.set(raw)

    def _spin(self):
        if not self._busy:
            return
        dots = ["Activating .  ", "Activating .. ", "Activating ..."][self._spinner_state % 3]
        self._spinner_state += 1
        try:
            self._btn.config(text=dots)
        except Exception:
            return
        self._spinner_id = self.after(400, self._spin)

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

        # Start spinner animation
        self._spinner_state = 0
        self._spin()

        # Clear old queue elements if any
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        # Safety timeout: if the worker thread never returns, force-reset the UI
        self._timeout_id = self.after(20000, self._reset_on_timeout)

        # Start thread-safe queue poller on the UI thread
        self.after(50, self._process_queue)

        def worker():
            from auth.client import activate
            try:
                result = activate(key, discord_id)
                self._queue.put({"status": "success", "result": result, "key": key, "discord_id": discord_id})
            except Exception as e:
                from auth.client import AuthResult
                result = AuthResult(ok=False, error=f"Client crash: {e}")
                self._queue.put({"status": "error", "result": result, "key": key, "discord_id": discord_id})

        threading.Thread(target=worker, daemon=True).start()

    def _process_queue(self):
        """Poll the safe queue from the UI main thread."""
        if not self._busy:
            return
        try:
            msg = self._queue.get_nowait()
            self._on_result(msg["result"], msg["key"], msg["discord_id"])
        except queue.Empty:
            self.after(50, self._process_queue)

    def _reset_on_timeout(self):
        if self._busy:
            # Cancel spinner
            if hasattr(self, '_spinner_id') and self._spinner_id:
                try:
                    self.after_cancel(self._spinner_id)
                except Exception:
                    pass
                self._spinner_id = None
            self._busy = False
            self._btn.config(state="normal", text="Activate", bg=ACCENT)
            self._set_status("Server did not respond in 20 seconds. Check your internet or try again.", RED)

    def _on_result(self, result, key: str, discord_id: str):
        self._busy = False

        # Cancel spinner
        if hasattr(self, '_spinner_id') and self._spinner_id:
            try:
                self.after_cancel(self._spinner_id)
            except Exception:
                pass
            self._spinner_id = None

        if self._timeout_id is not None:
            self.after_cancel(self._timeout_id)
            self._timeout_id = None
        self._btn.config(state="normal", text="Activate", bg=ACCENT)

        if result.ok:
            from auth.cache import save_session
            save_session(result.session_token, result.token_expires, key, discord_id)

            cfg = _load_config()
            cfg["license_key"]      = key
            cfg["discord_user_id"]  = discord_id
            _save_config(cfg)

            # DPAPI-protect the session token before storing in config
            try:
                token_bytes = result.session_token.encode("utf-8")
                protected = _dpapi_protect(token_bytes)
                # Store the DPAPI blob as base64 in config
                import base64 as _b64
                cfg2 = _load_config()
                cfg2["session_token_dpapi"] = _b64.b64encode(protected).decode("ascii")
                _save_config(cfg2)
            except Exception:
                pass  # non-critical — session token is already saved by save_session()

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
        # Show left-border bar with matching color for error/success, hide for dim
        if color in (RED, GREEN, YELLOW):
            self._status_bar.config(bg=color, width=3)
        else:
            self._status_bar.config(bg=BG_DARK, width=3)

    def _on_close(self):
        self.session = None
        self.destroy()


# ── Public entry point (Remains unchanged) ───────────────────────────────────

def run_preflight() -> dict:
    from auth.cache  import load_session, session_needs_refresh
    from auth.client import verify
    from auth.cache  import save_session

    root = tk.Tk()
    root.withdraw()

    if not _tos_accepted():
        tos = TOSWindow(root)
        root.wait_window(tos)
        if not tos.accepted:
            root.destroy()
            raise SystemExit(0)

    cfg = _load_config()
    saved_key        = cfg.get("license_key", "")
    saved_discord_id = cfg.get("discord_user_id", "")

    cached = load_session()
    if cached and not session_needs_refresh():
        root.destroy()
        return cached

    if saved_key and saved_discord_id:
        result = verify(saved_key, saved_discord_id)
        if result.ok:
            save_session(result.session_token, result.token_expires, saved_key, saved_discord_id)
            root.destroy()
            return {
                "session_token":   result.session_token,
                "token_expires":   result.token_expires,
                "key":             saved_key,
                "discord_user_id": saved_discord_id,
            }
        error_msg = result.error or "Verification failed"
        lic = LicenseWindow(root, prefill_key=saved_key, error=error_msg)
        root.wait_window(lic)
        if lic.session is None:
            root.destroy()
            raise SystemExit(0)
        root.destroy()
        return lic.session

    lic = LicenseWindow(root, prefill_key=saved_key)
    root.wait_window(lic)
    if lic.session is None:
        root.destroy()
        raise SystemExit(0)
    root.destroy()
    return lic.session
