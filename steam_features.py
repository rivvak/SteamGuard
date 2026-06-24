"""
steam_features.py — SteamGuard v1.4 feature modules
7 new features based on real Steam local data and registry.
Import from steamguard.py.
"""
from __future__ import annotations
import os, json, re, time, threading, sqlite3
import tkinter as tk
from tkinter import scrolledtext
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple
import urllib.request

try:
    import winreg
except ImportError:
    winreg = None  # non-Windows dev fallback

try:
    import vdf as _vdf_lib
    _VDF_AVAILABLE = True
except ImportError:
    _VDF_AVAILABLE = False

# ── Colours & fonts (mirror main app) ──────────────────────────────────────
BG_DARK  = "#0d1117"; BG_MID   = "#161b22"; BG_PANEL = "#1c2128"
BG_CARD  = "#21262d"; ACCENT   = "#58a6ff"; GREEN    = "#3fb950"
RED      = "#f85149"; YELLOW   = "#d29922"; PURPLE   = "#bc8cff"
TEXT_MAIN= "#e6edf3"; TEXT_DIM = "#8b949e"; BORDER   = "#30363d"
F_BODY   = ("Segoe UI", 10)
F_SMALL  = ("Segoe UI", 9)
F_MONO   = ("Consolas", 9)
F_HEAD   = ("Segoe UI", 12, "bold")


def _safe_grab(win) -> None:
    """Grab input for a Toplevel without crashing if it is not yet viewable.
    Retries shortly after the window is mapped."""
    try:
        win.grab_set()
    except tk.TclError:
        try:
            win.after(120, lambda: _safe_grab(win))
        except Exception:
            pass


def _center_over_parent(win, parent, w: int, h: int) -> None:
    """Center `win` over `parent`, falling back to screen-center if parent
    geometry is unavailable (e.g. parent not yet mapped)."""
    try:
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        if pw <= 1 or ph <= 1:
            raise ValueError
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
    except Exception:
        try:
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            x, y = (sw - w) // 2, (sh - h) // 2
        except Exception:
            x, y = 100, 100
    win.geometry(f"{w}x{h}+{max(0, x)}+{max(0, y)}")

# ═══════════════════════════════════════════════════════════════════════════
# SECTION 1 — VDF PARSER (fallback when vdf library not installed)
# ═══════════════════════════════════════════════════════════════════════════

def _parse_vdf_fallback(text: str) -> dict:
    """Minimal regex-based KeyValues1 parser. Lowercase keys."""
    # Strip line comments (// ...) outside of quotes to avoid false tokens.
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|(\{)|(\})', text)
    stack: list = [{}]
    pending_key: Optional[str] = None
    for quoted, open_b, close_b in tokens:
        if open_b:
            # A block opens. If we have a pending key, this block is its value
            # (already attached to the current parent). Otherwise it's an
            # anonymous block — attach under a synthetic key so it isn't lost.
            new: dict = {}
            if pending_key is not None:
                stack[-1][pending_key] = new
                pending_key = None
            else:
                stack[-1].setdefault("_anon", new)
            stack.append(new)
        elif close_b:
            # Discard any dangling key with no value so it does not leak
            # into the parent scope.
            pending_key = None
            if len(stack) > 1:
                stack.pop()
            else:
                break
        else:
            # Unescape common VDF escapes so values are usable.
            value = quoted.replace('\\\\', '\\').replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
            if pending_key is None:
                pending_key = value.lower()
            else:
                stack[-1][pending_key] = value
                pending_key = None
    return stack[0] if stack else {}


def _read_vdf(path: Path) -> dict:
    """Read and parse a Steam VDF text file. Returns {} on any error."""
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            text = path.read_text(encoding=enc, errors="replace")
            if _VDF_AVAILABLE:
                import io as _io
                return _vdf_lib.load(_io.StringIO(text), mapper=dict)
            return _parse_vdf_fallback(text)
        except Exception:
            continue
    return {}


def _read_vdf_with_backup(path: Path) -> dict:
    """Try path, then path + '_last' as backup (Steam's own recovery pattern)."""
    data = _read_vdf(path)
    if not data:
        bak = Path(str(path) + "_last")
        if bak.exists():
            data = _read_vdf(bak)
    return data


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 2 — STEAM LOCAL DATA HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _reg_get(root, subkey: str, name: str):
    """Read a single registry value; returns None on any error."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, subkey) as k:
            return winreg.QueryValueEx(k, name)[0]
    except Exception:
        return None


def get_steam_dir() -> Path:
    """Locate Steam installation directory from registry, multiple fallbacks."""
    if winreg:
        # HKCU paths (most reliable on single-user installs)
        for name in ("SteamPath", "SteamExe"):
            val = _reg_get(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", name)
            if val:
                p = Path(str(val).replace("/", "\\"))
                candidate = p.parent if name == "SteamExe" else p
                if candidate.exists():
                    return candidate
        # HKLM fallbacks
        for subkey in (r"SOFTWARE\WOW6432Node\Valve\Steam", r"SOFTWARE\Valve\Steam"):
            val = _reg_get(winreg.HKEY_LOCAL_MACHINE, subkey, "InstallPath")
            if val and Path(val).exists():
                return Path(val)
    return Path(r"C:\Program Files (x86)\Steam")


def get_all_steam_users(steam_dir: Optional[Path] = None) -> List[dict]:
    """
    Read Steam\\config\\loginusers.vdf.
    Returns list of dicts sorted by timestamp desc (most recent first).
    Fields: steamid64, accountname, personaname, timestamp, mostrecent, wantsoffline
    """
    steam_dir = steam_dir or get_steam_dir()
    path = steam_dir / "config" / "loginusers.vdf"
    raw = _read_vdf_with_backup(path)
    users_raw = raw.get("users", raw)  # top-level key may or may not be "users"
    result = []
    for sid64, info in users_raw.items():
        if not isinstance(info, dict):
            continue
        result.append({
            "steamid64":   str(sid64),
            "accountname": info.get("AccountName", info.get("accountname", "")),
            "personaname": info.get("PersonaName", info.get("personaname",
                           info.get("AccountName", info.get("accountname", sid64)))),
            "timestamp":   int(info.get("Timestamp", info.get("timestamp", 0)) or 0),
            "mostrecent":  str(info.get("MostRecent", info.get("mostrecent", "0"))) == "1",
            "wantsoffline":str(info.get("WantsOfflineMode", info.get("wantsofflinemode","0"))) == "1",
        })
    result.sort(key=lambda u: (u["mostrecent"], u["timestamp"]), reverse=True)
    return result


def get_active_steam_user(steam_dir: Optional[Path] = None) -> Tuple[str, str]:
    """Returns (steamid64, personaname) for the most-recent user."""
    users = get_all_steam_users(steam_dir)
    if users:
        u = users[0]
        return u["steamid64"], u["personaname"]
    return "", "Unknown"


def steamid64_to_accountid(sid64: str) -> str:
    """Convert SteamID64 to Steam3 account ID (used as userdata folder name)."""
    try:
        return str(int(sid64) - 76561197960265728)
    except Exception:
        return sid64


def get_running_appid_reg() -> Optional[int]:
    """Read Steam's RunningAppID. Checks the top-level Steam key first (where
    modern Steam writes it), then the legacy ActiveProcess sub-key."""
    if winreg is None:
        return None
    for subkey in (r"Software\Valve\Steam",
                   r"Software\Valve\Steam\ActiveProcess"):
        val = _reg_get(winreg.HKEY_CURRENT_USER, subkey, "RunningAppID")
        try:
            appid = int(val or 0)
        except (TypeError, ValueError):
            continue
        if appid != 0:
            return appid
    return None


def get_app_registry_info(appid: int) -> dict:
    """
    Read HKCU\\Software\\Valve\\Steam\\Apps\\{appid}.
    Returns {installed, running, updating} booleans.
    """
    out = {"installed": False, "running": False, "updating": False}
    if not winreg:
        return out
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf"Software\Valve\Steam\Apps\{appid}") as k:
            for field, reg_name in [("installed","Installed"),
                                     ("running","Running"),
                                     ("updating","Updating")]:
                try:
                    val, _ = winreg.QueryValueEx(k, reg_name)
                    out[field] = bool(int(val or 0))
                except Exception:
                    pass
    except Exception:
        pass
    return out


def get_owned_appids_reg() -> set:
    """Enumerate HKCU\\Software\\Valve\\Steam\\Apps subkeys → set of int appids."""
    ids: set = set()
    if not winreg:
        return ids
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\Apps")
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
                try:
                    ids.add(int(name))
                except ValueError:
                    pass
                i += 1
            except OSError:
                break
        winreg.CloseKey(root)
    except Exception:
        pass
    return ids


def get_shared_library_lock(steam_dir: Optional[Path] = None) -> dict:
    """
    Check if Steam's shared library is locked.
    Primary: parse Steam\\config\\sharedlibrarylocking.vdf (created by Steam when locked).
    Fallback: tail last 2 KB of content_log.txt for lock events.
    Returns {locked, locked_by, game_name}.
    """
    steam_dir = steam_dir or get_steam_dir()
    result = {"locked": False, "locked_by": "", "game_name": ""}

    lock_path = steam_dir / "config" / "sharedlibrarylocking.vdf"
    if lock_path.exists():
        try:
            raw = _read_vdf(lock_path)
            # Structure: root key -> {steamid/remoteid, appid, ...}
            inner = raw
            for v in raw.values():
                if isinstance(v, dict):
                    inner = v
                    break
            result["locked"] = True
            locked_by = inner.get("remoteid", inner.get("steamid", ""))
            result["game_name"] = str(inner.get("appid", ""))
            # Resolve steamid to persona name
            if locked_by:
                users = get_all_steam_users(steam_dir)
                for u in users:
                    if u["steamid64"] == str(locked_by):
                        locked_by = u["personaname"]
                        break
            result["locked_by"] = locked_by or "Another account"
            return result
        except Exception:
            result["locked"] = True
            result["locked_by"] = "Another account"
            return result

    # Fallback: content_log.txt tail
    log_path = steam_dir / "logs" / "content_log.txt"
    if log_path.exists():
        try:
            size = log_path.stat().st_size
            with open(log_path, "rb") as f:
                f.seek(max(0, size - 2048))
                tail = f.read().decode("utf-8", errors="replace")
            if "SharedLibraryLock" in tail or "shared library" in tail.lower():
                result["locked"] = True
                result["locked_by"] = "Another account (from log)"
        except Exception:
            pass

    return result


def get_steam_update_channel(steam_dir: Optional[Path] = None) -> str:
    """Return steam client beta branch: 'stable', 'publicbeta', etc."""
    steam_dir = steam_dir or get_steam_dir()
    beta_file = steam_dir / "package" / "beta"
    if beta_file.exists():
        try:
            ch = beta_file.read_text(encoding="utf-8", errors="replace").strip()
            return ch or "stable"
        except Exception:
            pass
    return "stable"


def is_running_on_steam_deck() -> bool:
    """Best-effort detection of whether THIS machine is a Steam Deck.
    On ordinary Windows PCs this returns False (expected)."""
    # SteamDeck registry flag (set by Steam client on Deck/SteamOS).
    if winreg is not None:
        val = _reg_get(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamDeck")
        try:
            if val is not None and int(val) == 1:
                return True
        except (TypeError, ValueError):
            pass
    # Environment hint present on SteamOS.
    if os.environ.get("SteamDeck") == "1" or os.environ.get("SteamOS") == "1":
        return True
    return False


def get_authorized_devices(steam_dir: Optional[Path] = None) -> List[dict]:
    """
    Read legacy authorized device blocks from Steam\\config\\config.vdf.
    Returns list of {device_id, name, device_type, is_deck, last_seen_ts}.
    """
    steam_dir = steam_dir or get_steam_dir()
    cfg = _read_vdf(steam_dir / "config" / "config.vdf")
    devices: List[dict] = []

    def _walk(node, depth=0):
        if not isinstance(node, dict) or depth > 8:
            return
        for k, v in node.items():
            if "authorizeddevice" in k.lower() or "device" in k.lower():
                if isinstance(v, dict):
                    for dev_id, dev_info in v.items():
                        if isinstance(dev_info, dict):
                            dtype = str(dev_info.get("type", dev_info.get("device_type",""))).lower()
                            dname = str(dev_info.get("name", dev_id))
                            ts_raw = dev_info.get("timestamp", dev_info.get("lastseen","0"))
                            devices.append({
                                "device_id":    dev_id,
                                "name":         dname,
                                "device_type":  dtype,
                                "is_deck":      ("steamdeck" in dtype or "deck" in dname.lower()
                                                  or dtype in ("15","deck")),
                                "last_seen_ts": int(ts_raw or 0),
                            })
            if isinstance(v, dict):
                _walk(v, depth + 1)

    _walk(cfg)
    return devices


def get_family_sharing_enabled(steam_dir: Optional[Path] = None) -> bool:
    """Check Steam\\config\\config.vdf for family sharing enabled flag."""
    steam_dir = steam_dir or get_steam_dir()
    cfg = _read_vdf(steam_dir / "config" / "config.vdf")
    def _search(node, depth=0):
        if not isinstance(node, dict) or depth > 6:
            return None
        for k, v in node.items():
            if k.lower() in ("familysharing", "familylibrary", "familygroup"):
                if isinstance(v, dict):
                    enabled = v.get("enabled", v.get("Enabled", "1"))
                    return str(enabled) != "0"
            result = _search(v, depth+1)
            if result is not None:
                return result
        return None
    found = _search(cfg)
    return found if found is not None else True  # default enabled


def get_local_user_activity(steam_dir: Path, account_id: str) -> dict:
    """
    Read userdata/{account_id}/config/localconfig.vdf
    Returns dict of {appid: {LastPlayed, Playtime, ...}}
    """
    path = steam_dir / "userdata" / str(account_id) / "config" / "localconfig.vdf"
    raw = _read_vdf(path)
    # Navigate: UserLocalConfigStore > Software > Valve > Steam > Apps
    try:
        apps = (raw.get("UserLocalConfigStore", raw)
                   .get("Software", raw.get("UserLocalConfigStore", raw))
                   .get("Valve", {})
                   .get("Steam", {})
                   .get("Apps", {}))
    except Exception:
        apps = {}
    return {int(k): v for k, v in apps.items() if str(k).isdigit() and isinstance(v, dict)}


def get_steam_offline_flag() -> Optional[int]:
    """Read HKCU\\Software\\Valve\\Steam\\Offline. Returns 1=offline, 0=online, None=unknown."""
    if winreg is None:
        return None
    val = _reg_get(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "Offline")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def get_wants_offline(steam_dir: Optional[Path] = None) -> bool:
    """Whether the most-recent Steam user has WantsOfflineMode set in loginusers.vdf."""
    users = get_all_steam_users(steam_dir)
    return bool(users and users[0].get("wantsoffline"))


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 3 — STEAM STORE API + SQLITE CACHE
# ═══════════════════════════════════════════════════════════════════════════

_CACHE_DB: Optional[sqlite3.Connection] = None

def _get_cache_db() -> sqlite3.Connection:
    global _CACHE_DB
    if _CACHE_DB is None:
        db_path = Path(os.environ.get("APPDATA","~")).expanduser() / "SteamGuard" / "store_cache.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_DB = sqlite3.connect(str(db_path), check_same_thread=False)
        _CACHE_DB.execute("""CREATE TABLE IF NOT EXISTS app_cache(
            appid INTEGER PRIMARY KEY, data TEXT NOT NULL, fetched_at INTEGER NOT NULL)""")
        _CACHE_DB.commit()
    return _CACHE_DB


def fetch_app_details(appid: int, force: bool = False) -> Optional[dict]:
    """
    Fetch appdetails from Steam Store API (no auth).
    24h SQLite cache. Returns 'data' dict or None.
    """
    if not appid or appid <= 0:
        return None
    db = _get_cache_db()
    now = int(time.time())
    TTL = 86400

    if not force:
        row = db.execute("SELECT data, fetched_at FROM app_cache WHERE appid=?",
                         (appid,)).fetchone()
        if row and (now - row[1]) < TTL:
            try:
                return json.loads(row[0])
            except Exception:
                pass

    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&l=english"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "SteamGuard/1.4",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = json.loads(r.read())
        entry = raw.get(str(appid), {})
        if not entry.get("success"):
            return None
        data = entry.get("data", {})
        db.execute("INSERT OR REPLACE INTO app_cache VALUES (?,?,?)",
                   (appid, json.dumps(data), now))
        db.commit()
        return data
    except Exception:
        return None


def classify_game(appid: int, name: str = "") -> dict:
    """
    Classify a game for Family Sharing eligibility.

    Uses:
    - Category 62 = Family Sharing supported
    - Category 8  = VAC enabled
    - ext_user_account_notice / drm_notice = third-party account
    - is_free = F2P (not shareable per Steam rules)

    Returns dict with shareable, vac_enabled, third_party, free_to_play,
    excluded_reason, risk_level, badges, dlc_appids.
    """
    out = {
        "shareable": None,  # None = unknown
        "vac_enabled": False,
        "third_party": False,
        "free_to_play": False,
        "excluded_reason": "",
        "risk_level": "unknown",
        "badges": [],
        "dlc_appids": [],
    }

    data = fetch_app_details(appid)
    if data is None:
        out["badges"] = ["❓ Unknown"]
        return out

    cats = data.get("categories", [])
    cat_ids = {int(c.get("id", 0)) for c in cats if isinstance(c, dict)}

    # Family Sharing support (category 62)
    out["shareable"] = 62 in cat_ids

    # VAC (category 8)
    if 8 in cat_ids:
        out["vac_enabled"] = True

    # Free-to-play
    if data.get("is_free", False):
        out["free_to_play"] = True
        out["shareable"] = False
        out["excluded_reason"] = "Free-to-play games cannot be shared"

    # Third-party account
    notice = str(data.get("ext_user_account_notice", "") or "")
    drm    = str(data.get("drm_notice", "") or "")
    if notice or drm:
        out["third_party"] = True

    # Build badges
    if not out["shareable"] and out["free_to_play"]:
        out["badges"].append("❌ F2P — Not Shareable")
        out["risk_level"] = "high"
    elif out["shareable"] is False:
        out["badges"].append("❌ Publisher Excluded")
        out["risk_level"] = "high"
    elif out["shareable"]:
        out["badges"].append("✅ Shareable")
        out["risk_level"] = "low"
    else:
        out["badges"].append("❓ Unknown")

    if out["vac_enabled"]:
        out["badges"].append("⚠ VAC Enabled")
        if out["risk_level"] == "low":
            out["risk_level"] = "medium"

    if out["third_party"]:
        short_notice = notice[:60] if notice else "Third-party account required"
        out["badges"].append(f"⚠ {short_notice}")

    # DLC list
    out["dlc_appids"] = [int(d) for d in data.get("dlc", []) if str(d).isdigit()]

    return out


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 4 — PRE-LAUNCH KICK WARNING
# ═══════════════════════════════════════════════════════════════════════════

class PreLaunchWarning:
    """
    Background monitor: fires on_warning_cb(appid, game_name, locked_by)
    when a new game launch is detected AND the shared library is locked.
    Uses registry polling (500ms) — catches launch within ~0.5s.
    """

    def __init__(self, steam_dir: Optional[Path], on_warning_cb):
        self._steam_dir = Path(steam_dir) if steam_dir else None
        self._on_warning = on_warning_cb
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._warned: set = set()

    def start(self):
        if self._running:
            return
        self._running = True
        # Pre-seed _warned with whatever is ALREADY running so that a game
        # which launched before the app started never triggers a warning.
        startup_appid = get_running_appid_reg()
        if startup_appid:
            self._warned.add(startup_appid)
        self._thread = threading.Thread(target=self._loop, daemon=True, name="PreLaunchWarn")
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        prev = get_running_appid_reg()
        while self._running:
            time.sleep(0.5)
            try:
                cur = get_running_appid_reg()
                if cur and cur != prev and cur not in self._warned:
                    lock = get_shared_library_lock(self._steam_dir)
                    if lock["locked"]:
                        self._warned.add(cur)
                        game_name = f"AppID {cur}"
                        try:
                            d = fetch_app_details(cur)
                            if d:
                                game_name = d.get("name", game_name)
                        except Exception:
                            pass
                        try:
                            self._on_warning(cur, game_name, lock.get("locked_by",""))
                        except Exception:
                            pass
                if not cur:
                    self._warned.clear()
                prev = cur
            except Exception:
                pass

    def check_launch_risk(self, appid: int) -> dict:
        """Synchronous point-in-time check."""
        lock = get_shared_library_lock(self._steam_dir)
        reg  = get_app_registry_info(appid)
        risk = lock["locked"] or reg.get("running", False)
        msg  = ""
        if lock["locked"]:
            msg = (f"⚠  {lock.get('locked_by','Someone')} is currently using your "
                   f"shared library. Launching will kick them out.")
        elif reg.get("running"):
            msg = "⚠  This game is already running on another account."
        return {"risk": risk, "message": msg,
                "locked_by": lock.get("locked_by",""),
                "game_name": lock.get("game_name","")}


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 5 — "WHY IS THIS LOCKED?" DIALOG
# ═══════════════════════════════════════════════════════════════════════════

class WhyLockedDialog(tk.Toplevel):
    """Explains exactly why the Steam shared library is locked."""

    def __init__(self, parent, steam_dir: Optional[Path] = None):
        super().__init__(parent)
        self._steam_dir = Path(steam_dir) if steam_dir else get_steam_dir()
        self.title("Library Lock Diagnostics")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        _center_over_parent(self, parent, 480, 400)
        _safe_grab(self)
        self._auto_id: Optional[str] = None
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self._refresh()

    def _build(self):
        # Header
        hdr = tk.Frame(self, bg=BG_PANEL, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="🔒  Library Lock Diagnostics",
                 bg=BG_PANEL, fg=TEXT_MAIN, font=F_HEAD).pack(side="left", padx=16, pady=12)
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Status row
        status_row = tk.Frame(self, bg=BG_DARK)
        status_row.pack(fill="x", padx=16, pady=(14,0))
        self._icon_lbl = tk.Label(status_row, text="⏳", bg=BG_DARK,
                                   fg=YELLOW, font=("Segoe UI Emoji", 28))
        self._icon_lbl.pack(side="left", padx=(0,12))
        txt_f = tk.Frame(status_row, bg=BG_DARK)
        txt_f.pack(side="left", fill="x", expand=True)
        self._title_lbl = tk.Label(txt_f, text="Checking…", bg=BG_DARK,
                                    fg=TEXT_MAIN, font=("Segoe UI", 11, "bold"), anchor="w")
        self._title_lbl.pack(fill="x")
        self._sub_lbl = tk.Label(txt_f, text="", bg=BG_DARK, fg=TEXT_DIM,
                                  font=F_SMALL, anchor="w", wraplength=320)
        self._sub_lbl.pack(fill="x")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(12,0))

        # Details grid
        grid = tk.Frame(self, bg=BG_DARK)
        grid.pack(fill="x", padx=16, pady=(10,0))
        self._vals: dict = {}
        rows = [("🔒 Locked by","locked_by"), ("🎮 Game","game"),
                ("⏱ Since","since"), ("👤 Active user","user"),
                ("✅ How to fix","fix")]
        for i, (label, key) in enumerate(rows):
            tk.Label(grid, text=label, bg=BG_DARK, fg=TEXT_DIM,
                     font=("Segoe UI", 8, "bold"), width=16, anchor="w"
                     ).grid(row=i, column=0, sticky="w", pady=2)
            v = tk.Label(grid, text="—", bg=BG_DARK, fg=TEXT_MAIN,
                         font=F_SMALL, anchor="w", wraplength=280, justify="left")
            v.grid(row=i, column=1, sticky="w", padx=(8,0), pady=2)
            self._vals[key] = v

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(10,0))

        btn_row = tk.Frame(self, bg=BG_DARK)
        btn_row.pack(fill="x", padx=16, pady=10)
        tk.Button(btn_row, text="↻  Refresh", bg=BG_CARD, fg=ACCENT,
                  font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                  command=self._refresh).pack(side="left", ipady=5, ipadx=12)
        tk.Button(btn_row, text="✕  Close", bg=BG_CARD, fg=TEXT_DIM,
                  font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                  command=self._on_close).pack(side="right", ipady=5, ipadx=12)

        self._schedule_auto_refresh()

    def _schedule_auto_refresh(self):
        if self.winfo_exists():
            self._auto_id = self.after(5000, self._auto_refresh)

    def _auto_refresh(self):
        if self.winfo_exists():
            self._refresh()
            self._schedule_auto_refresh()

    def _on_close(self):
        if self._auto_id is not None:
            try:
                self.after_cancel(self._auto_id)
            except Exception:
                pass
            self._auto_id = None
        self.destroy()

    def _refresh(self):
        lock  = get_shared_library_lock(self._steam_dir)
        _, active_name = get_active_steam_user(self._steam_dir)
        appid = get_running_appid_reg()
        game_name = lock.get("game_name","")
        if appid and not game_name:
            try:
                d = fetch_app_details(appid)
                game_name = (d or {}).get("name", f"AppID {appid}")
            except Exception:
                game_name = f"AppID {appid}"

        if not lock["locked"]:
            self._icon_lbl.config(text="✅", fg=GREEN)
            self._title_lbl.config(text="Library is not locked", fg=GREEN)
            self._sub_lbl.config(text="No active shared library lock detected.")
            for v in self._vals.values():
                v.config(text="—", fg=TEXT_MAIN)
            self._vals["fix"].config(text="Launch your game normally.", fg=GREEN)
        else:
            self._icon_lbl.config(text="🔒", fg=RED)
            self._title_lbl.config(text="Shared Library is Locked", fg=RED)
            self._sub_lbl.config(text="Another account on this machine is using your library.")
            self._vals["locked_by"].config(text=lock.get("locked_by","Unknown") or "Unknown")
            self._vals["game"].config(text=game_name or "—")
            self._vals["since"].config(text="Active now")
            self._vals["user"].config(text=active_name)
            self._vals["fix"].config(
                text="1. Ask the other user to quit their game\n"
                     "2. Or wait — lock releases automatically when they quit\n"
                     "3. Or launch a game you own to reclaim priority",
                fg=YELLOW)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 6 — GAME BADGE PANEL (inline in game card)
# ═══════════════════════════════════════════════════════════════════════════

class GameBadgePanel(tk.Frame):
    """
    Lightweight inline badge strip showing VAC/sharing status.
    Call update_game(appid, name) when the detected game changes.
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=BG_DARK, height=1, **kwargs)
        self.pack_propagate(False)
        self._labels: List[tk.Label] = []
        self._gen = 0           # generation token to drop stale fetches
        self._cur_appid: Optional[int] = None

    def _clear(self):
        for lbl in self._labels:
            try:
                lbl.destroy()
            except Exception:
                pass
        self._labels.clear()

    def update_game(self, appid: Optional[int], name: str = ""):
        self._gen += 1
        gen = self._gen
        self._cur_appid = appid if appid else None
        self._clear()
        if not appid:
            self.config(height=1)
            return
        self.config(height=20)
        placeholder = tk.Label(self, text="Checking…",
                                bg=BG_DARK, fg=TEXT_DIM, font=("Segoe UI", 7))
        placeholder.pack(side="left", padx=(4,4))
        self._labels.append(placeholder)
        threading.Thread(target=self._fetch, args=(appid, name, gen),
                         daemon=True).start()

    def _fetch(self, appid: int, name: str, gen: int):
        try:
            c = classify_game(appid, name)
        except Exception:
            c = {"badges": []}
        try:
            if self.winfo_exists():
                self.after(0, lambda: self._show(c, gen))
        except (tk.TclError, RuntimeError):
            pass

    def _show(self, c: dict, gen: int = None):
        # Drop results from a superseded update_game() call.
        if gen is not None and gen != self._gen:
            return
        if not self.winfo_exists():
            return
        self._clear()
        badges = c.get("badges", []) if isinstance(c, dict) else []
        if not badges:
            self.config(height=1)
            return
        self.config(height=20)
        COLOR = {"✅": GREEN, "❌": RED, "⚠": YELLOW, "❓": TEXT_DIM}
        for badge in badges:
            col = TEXT_DIM
            for pfx, clr in COLOR.items():
                if badge.startswith(pfx):
                    col = clr
                    break
            lbl = tk.Label(self, text=badge, bg=BG_DARK, fg=col,
                           font=("Segoe UI", 7, "bold"))
            lbl.pack(side="left", padx=(4,6))
            self._labels.append(lbl)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 7 — DLC COPY ADVISOR DIALOG
# ═══════════════════════════════════════════════════════════════════════════

class DLCAdvisorDialog(tk.Toplevel):
    """Shows DLC coverage for current game and recommends best copy to use."""

    def __init__(self, parent, steam_dir: Optional[Path] = None,
                 appid: Optional[int] = None, game_name: str = ""):
        """Called as DLCAdvisorDialog(parent, steam_dir). The current game is
        auto-detected from the Steam registry when appid is not supplied."""
        super().__init__(parent)
        self._steam_dir = Path(steam_dir) if steam_dir else get_steam_dir()
        # Auto-detect the running game if no explicit appid was passed.
        if appid is None:
            appid = get_running_appid_reg()
        try:
            self._appid = int(appid) if appid else None
        except (TypeError, ValueError):
            self._appid = None
        self._game_name = game_name or (f"AppID {self._appid}" if self._appid else "")
        self.title("DLC Advisor")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        _center_over_parent(self, parent, 520, 480)
        _safe_grab(self)
        self._build()
        threading.Thread(target=self._load, daemon=True).start()

    def _build(self):
        hdr = tk.Frame(self, bg=BG_PANEL, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        _title = self._game_name[:32] if self._game_name else "No game detected"
        tk.Label(hdr, text=f"💿  DLC Advisor — {_title}",
                 bg=BG_PANEL, fg=TEXT_MAIN, font=F_HEAD).pack(side="left", padx=16, pady=12)
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        self._loading_lbl = tk.Label(self, text="Fetching DLC data from Steam Store…",
                                      bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL)
        self._loading_lbl.pack(pady=20)

        self._results_frame = tk.Frame(self, bg=BG_DARK)
        inner = tk.Frame(self._results_frame, bg=BG_DARK)
        inner.pack(fill="both", expand=True, padx=16, pady=(10,0))
        tk.Label(inner, text="DLC COVERAGE (ACTIVE ACCOUNT)",
                 bg=BG_DARK, fg=TEXT_DIM, font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(0,4))
        self._dlc_text = scrolledtext.ScrolledText(
            inner, bg=BG_PANEL, fg=TEXT_MAIN, font=F_MONO,
            relief="flat", bd=0, state="disabled", height=12)
        self._dlc_text.pack(fill="both", expand=True)

        rec_frame = tk.Frame(self._results_frame, bg=BG_CARD)
        rec_frame.pack(fill="x", padx=16, pady=(8,0))
        tk.Label(rec_frame, text="💡 RECOMMENDATION", bg=BG_CARD, fg=ACCENT,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=12, pady=(6,2))
        self._rec_lbl = tk.Label(rec_frame, text="…", bg=BG_CARD, fg=TEXT_MAIN,
                                  font=F_SMALL, wraplength=470, justify="left", anchor="w")
        self._rec_lbl.pack(anchor="w", padx=12, pady=(0,8))

        tk.Button(self._results_frame, text="Close", bg=BG_CARD, fg=TEXT_DIM,
                  font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                  command=self.destroy).pack(pady=8, ipady=5, ipadx=20)

    def _load(self):
        # No game detected — show a friendly message instead of crashing.
        if not self._appid:
            msg = ("No running game detected.\n\n"
                   "Launch a Steam game first, then reopen the DLC Advisor "
                   "to see DLC coverage for that game.")
            self.after(0, lambda: self._show(
                msg, "Start a game to analyze its DLC sharing coverage."))
            return

        try:
            c = classify_game(self._appid, self._game_name)
        except Exception:
            c = {}
        dlc_ids = c.get("dlc_appids", []) if isinstance(c, dict) else []
        owned   = get_owned_appids_reg()

        # Fetch DLC names (max 15, 0.1s delay between)
        dlc_names: dict = {}
        for did in dlc_ids[:15]:
            try:
                d = fetch_app_details(did)
                dlc_names[did] = (d or {}).get("name", f"DLC {did}")
            except Exception:
                dlc_names[did] = f"DLC AppID {did}"
            time.sleep(0.1)

        lines: list = []
        if not dlc_names:
            lines.append("No DLC found for this game in the Steam Store.")
            lines.append("(This game may have no DLC, or the Store data is unavailable.)")
            rec = "No DLC detected. Any copy of this game will work equally."
        else:
            owned_dlc   = [(did, nm) for did, nm in dlc_names.items() if did in owned]
            missing_dlc = [(did, nm) for did, nm in dlc_names.items() if did not in owned]
            lines.append(f"Game: {self._game_name}")
            lines.append(f"Total DLC: {len(dlc_names)}  |  Owned on this PC: {len(owned_dlc)}")
            lines.append("")
            if owned_dlc:
                lines.append("OWNED / INSTALLED:")
                for _, nm in owned_dlc:
                    lines.append(f"  ✅ {nm}")
            if missing_dlc:
                lines.append("")
                lines.append("NOT OWNED / NOT INSTALLED:")
                for _, nm in missing_dlc:
                    lines.append(f"  ❌ {nm}")
            if len(owned_dlc) == len(dlc_names):
                rec = "✅ You own all DLC for this game. Full experience available."
            elif owned_dlc:
                rec = (f"You have {len(owned_dlc)}/{len(dlc_names)} DLC items on this PC. "
                       f"If a family member owns the missing DLC and their copy is used, "
                       f"you may get access to it during the shared session.")
            else:
                rec = (f"None of the {len(dlc_names)} DLC items are installed on this PC. "
                       f"Check if a family member owns the DLC — launching from their copy "
                       f"may grant access to it during your session per Steam Family rules.")

        try:
            self.after(0, lambda: self._show("\n".join(lines), rec))
        except (tk.TclError, RuntimeError):
            pass

    def _show(self, text: str, rec: str):
        if not self.winfo_exists():
            return
        self._loading_lbl.pack_forget()
        self._results_frame.pack(fill="both", expand=True)
        self._dlc_text.config(state="normal")
        self._dlc_text.delete("1.0", "end")
        self._dlc_text.insert("end", text)
        self._dlc_text.config(state="disabled")
        self._rec_lbl.config(text=rec)


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 8 — STEAM DECK HEALTH CHECK DIALOG
# ═══════════════════════════════════════════════════════════════════════════

class SteamDeckHealthDialog(tk.Toplevel):
    """Diagnoses Family Sharing issues for Steam Deck users."""

    def __init__(self, parent, steam_dir: Optional[Path] = None):
        super().__init__(parent)
        self._steam_dir = Path(steam_dir) if steam_dir else get_steam_dir()
        self.title("Steam Deck Health Check")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        _center_over_parent(self, parent, 500, 520)
        _safe_grab(self)
        self._items: List[Tuple[tk.Label, tk.Label]] = []
        self._build()
        threading.Thread(target=self._run_checks, daemon=True).start()

    def _build(self):
        hdr = tk.Frame(self, bg=BG_PANEL, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="🎮  Steam Deck Health Check",
                 bg=BG_PANEL, fg=TEXT_MAIN, font=F_HEAD).pack(side="left", padx=16, pady=12)
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        tk.Label(self, text="Diagnosing Family Sharing and Steam Deck authorization…",
                 bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL).pack(anchor="w", padx=16, pady=(10,4))

        cl = tk.Frame(self, bg=BG_DARK)
        cl.pack(fill="x", padx=16)
        check_labels = [
            "Steam client update channel",
            "Family Sharing enabled",
            "Authorized Steam Deck devices",
            "Stale device authorizations (>180 days)",
            "Shared library lock state",
            "Active Steam user",
        ]
        for label in check_labels:
            row = tk.Frame(cl, bg=BG_CARD)
            row.pack(fill="x", pady=2)
            icon = tk.Label(row, text="⏳", bg=BG_CARD, fg=YELLOW,
                            font=("Segoe UI Emoji", 12), width=3)
            icon.pack(side="left", padx=(10,8), pady=7)
            tk.Label(row, text=label, bg=BG_CARD, fg=TEXT_MAIN,
                     font=F_SMALL, anchor="w").pack(side="left", fill="x", expand=True)
            det = tk.Label(row, text="", bg=BG_CARD, fg=TEXT_DIM,
                           font=("Segoe UI", 7), anchor="e")
            det.pack(side="right", padx=10)
            self._items.append((icon, det))

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(10,0))
        tk.Label(self, text="RECOMMENDATIONS", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=16, pady=(6,2))
        self._rec_box = scrolledtext.ScrolledText(
            self, bg=BG_PANEL, fg=TEXT_MAIN, font=F_MONO,
            relief="flat", bd=0, state="disabled", height=5)
        self._rec_box.pack(fill="x", padx=16)
        tk.Button(self, text="Close", bg=BG_CARD, fg=TEXT_DIM,
                  font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                  command=self.destroy).pack(pady=8, ipady=5, ipadx=20)

    def _set(self, i: int, status, detail: str):
        """status: True=ok (green ✅), False=warn (yellow ⚠), 'info'=neutral (blue ℹ)."""
        if status == "info":
            icon, icon_fg, det_fg = "ℹ", ACCENT, TEXT_DIM
        elif status:
            icon, icon_fg, det_fg = "✅", GREEN, TEXT_MAIN
        else:
            icon, icon_fg, det_fg = "⚠", YELLOW, YELLOW
        def _do():
            if not self.winfo_exists(): return
            self._items[i][0].config(text=icon, fg=icon_fg)
            self._items[i][1].config(text=detail, fg=det_fg)
        try:
            self.after(0, _do)
        except (tk.TclError, RuntimeError):
            pass

    def _run_checks(self):
        recs: list = []
        now = int(time.time())
        is_deck = is_running_on_steam_deck()

        # 0. Update channel
        ch = get_steam_update_channel(self._steam_dir)
        ok = ch in ("stable","")
        self._set(0, ok, ch or "stable")
        if not ok:
            recs.append(f"• On '{ch}' Steam branch — Family Sharing behavior may differ from stable.")

        # 1. Family Sharing enabled
        enabled = get_family_sharing_enabled(self._steam_dir)
        self._set(1, enabled, "Enabled" if enabled else "DISABLED")
        if not enabled:
            recs.append("• Family Sharing appears disabled in config. Go to Steam → Settings → Family.")

        # 2. Authorized Deck devices.
        # On a non-Deck Windows PC, having no Deck is normal — show neutral info,
        # not a warning, so the check doesn't "fail" on ordinary machines.
        devices = get_authorized_devices(self._steam_dir)
        decks   = [d for d in devices if d["is_deck"]]
        if decks:
            self._set(2, True, f"{len(decks)} Deck(s), {len(devices)} total")
        elif is_deck:
            # This machine IS a Deck but isn't in the authorized list — real issue.
            self._set(2, False, "This Deck not authorized")
            recs.append("• This Steam Deck is not in the authorized device list. "
                        "Log into Steam on it and enable Family Sharing.")
        else:
            self._set(2, "info",
                      f"No Deck ({len(devices)} other device(s))" if devices else "No Deck (PC)")
            recs.append("• No Steam Deck detected on this PC. This is normal if you "
                        "don't own a Deck; otherwise log into Steam on your Deck first.")

        # 3. Stale devices
        stale = [d for d in devices
                 if d["last_seen_ts"] and (now - d["last_seen_ts"]) > 180 * 86400]
        if not devices:
            self._set(3, "info", "No devices listed")
        else:
            self._set(3, len(stale) == 0, f"{len(stale)} stale" if stale else "All recent")
        if stale:
            names = ", ".join(d["name"] for d in stale[:3])
            recs.append(f"• {len(stale)} device(s) not seen in 180+ days: {names}. "
                        f"Deauthorize via Steam → Settings → Family.")

        # 4. Lock state
        lock = get_shared_library_lock(self._steam_dir)
        self._set(4, not lock["locked"],
                  f"Locked by {lock['locked_by']}" if lock["locked"] else "Not locked")
        if lock["locked"]:
            recs.append(f"• Library locked by {lock['locked_by']}. Ask them to quit their game first.")

        # 5. Active user
        sid, name = get_active_steam_user(self._steam_dir)
        self._set(5, bool(sid), name if sid else "No user detected")
        if not sid:
            recs.append("• No active Steam user detected. Ensure Steam is running.")

        rec_text = ("\n".join(recs) if recs
                    else "✅ Everything looks healthy! Your Steam Deck Family Sharing setup is good.")

        def _show():
            if not self.winfo_exists(): return
            self._rec_box.config(state="normal")
            self._rec_box.delete("1.0","end")
            self._rec_box.insert("end", rec_text)
            self._rec_box.config(state="disabled")
        try:
            self.after(0, _show)
        except (tk.TclError, RuntimeError):
            pass


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 9 — OFFLINE READINESS CHECKER
# ═══════════════════════════════════════════════════════════════════════════

class OfflineReadinessDialog(tk.Toplevel):
    """Checks whether a game can be played offline in shared mode."""

    def __init__(self, parent, steam_dir: Optional[Path] = None,
                 appid: Optional[int] = None, game_name: str = ""):
        """Called as OfflineReadinessDialog(parent, steam_dir). The current game
        is auto-detected from the Steam registry when appid is not supplied."""
        super().__init__(parent)
        self._steam_dir = Path(steam_dir) if steam_dir else get_steam_dir()
        if appid is None:
            appid = get_running_appid_reg()
        try:
            self._appid = int(appid) if appid else None
        except (TypeError, ValueError):
            self._appid = None
        self._game_name = game_name or (f"AppID {self._appid}" if self._appid else "")
        self.title("Offline Readiness Checker")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        _center_over_parent(self, parent, 460, 400)
        _safe_grab(self)
        self._items: List[Tuple[tk.Label, tk.Label]] = []
        self._build()
        threading.Thread(target=self._run_checks, daemon=True).start()

    def _build(self):
        hdr = tk.Frame(self, bg=BG_PANEL, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        title_text = self._game_name[:30] if self._game_name else "No game selected"
        tk.Label(hdr, text=f"✈  Offline Readiness — {title_text}",
                 bg=BG_PANEL, fg=TEXT_MAIN, font=("Segoe UI", 11, "bold")).pack(
                 side="left", padx=16, pady=14)
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        tk.Label(self, text="Can you play this game offline on this device?",
                 bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL).pack(anchor="w", padx=16, pady=(10,4))

        cl = tk.Frame(self, bg=BG_DARK)
        cl.pack(fill="x", padx=16)
        checks = [
            "Game is installed locally",
            "Game supports Family Sharing",
            "Game launched online at least once",
            "Steam Cloud save data present",
            "Steam offline mode available",
        ]
        for label in checks:
            row = tk.Frame(cl, bg=BG_CARD)
            row.pack(fill="x", pady=2)
            icon = tk.Label(row, text="⏳", bg=BG_CARD, fg=YELLOW,
                            font=("Segoe UI Emoji", 12), width=3)
            icon.pack(side="left", padx=(10,8), pady=7)
            tk.Label(row, text=label, bg=BG_CARD, fg=TEXT_MAIN,
                     font=F_SMALL, anchor="w").pack(side="left", fill="x", expand=True)
            det = tk.Label(row, text="", bg=BG_CARD, fg=TEXT_DIM,
                           font=("Segoe UI", 7), anchor="e")
            det.pack(side="right", padx=10)
            self._items.append((icon, det))

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(12,0))
        self._verdict = tk.Label(self, text="", bg=BG_DARK, fg=TEXT_MAIN,
                                  font=("Segoe UI", 10, "bold"),
                                  wraplength=420, justify="center")
        self._verdict.pack(pady=10)
        tk.Button(self, text="Close", bg=BG_CARD, fg=TEXT_DIM, font=F_SMALL,
                  relief="flat", bd=0, cursor="hand2",
                  command=self.destroy).pack(pady=6, ipady=5, ipadx=20)

    def _set(self, i: int, status, detail: str = ""):
        """status: True=ok, False=warn, 'info'=neutral."""
        if status == "info":
            icon, fg = "ℹ", ACCENT
        elif status:
            icon, fg = "✅", GREEN
        else:
            icon, fg = "⚠", YELLOW
        def _do():
            if not self.winfo_exists(): return
            self._items[i][0].config(text=icon, fg=fg)
            self._items[i][1].config(text=detail)
        try:
            self.after(0, _do)
        except (tk.TclError, RuntimeError):
            pass

    def _run_checks(self):
        if not self._appid:
            def _no_game():
                if not self.winfo_exists(): return
                self._verdict.config(
                    text="⚠ No running game detected.\nLaunch a Steam game first, "
                         "then reopen this checker.", fg=YELLOW)
            try:
                self.after(0, _no_game)
            except (tk.TclError, RuntimeError):
                pass
            return

        results: list = []
        sid, _ = get_active_steam_user(self._steam_dir)
        account_id = steamid64_to_accountid(sid) if sid else ""

        # 0. Installed?
        reg = get_app_registry_info(self._appid)
        installed = reg.get("installed", False)
        self._set(0, installed, "Installed" if installed else "NOT installed")
        results.append(installed)

        # 1. Supports sharing?
        c = classify_game(self._appid, self._game_name)
        shareable = c.get("shareable") is True
        detail = "Shareable"
        if not shareable:
            detail = c.get("excluded_reason","May be excluded") or "Not marked shareable"
        if c.get("vac_enabled"):
            detail += " (VAC)"
        self._set(1, shareable, detail)
        results.append(shareable)

        # 2. Launched before? (localconfig.vdf LastPlayed or userdata dir)
        launched = False
        if account_id:
            try:
                activity = get_local_user_activity(self._steam_dir, account_id)
            except Exception:
                activity = {}
            info = activity.get(self._appid, {})
            def _as_int(v):
                try:
                    return int(v or 0)
                except (TypeError, ValueError):
                    return 0
            lp = _as_int(info.get("LastPlayed", info.get("lastplayed", 0)))
            pt = _as_int(info.get("Playtime", info.get("playtime", 0)))
            launched = lp > 0 or pt > 0
            if not launched:
                # fallback: check userdata/{accountid}/{appid}/ exists
                try:
                    ud = self._steam_dir / "userdata" / str(account_id) / str(self._appid)
                    launched = ud.exists()
                except Exception:
                    launched = False
        self._set(2, launched, "Yes" if launched else "Not detected — launch online first")
        results.append(launched)

        # 3. Cloud save present?
        cloud = False
        if account_id:
            try:
                remote = (self._steam_dir / "userdata" / str(account_id)
                          / str(self._appid) / "remote")
                cloud = remote.exists() and any(True for _ in remote.iterdir())
            except Exception:
                cloud = False
        # Informational only — neutral icon, never a warning.
        self._set(3, True if cloud else "info",
                  "Cloud data found" if cloud else "No cloud data (may be normal)")
        results.append(True)  # not blocking — informational only

        # 4. Offline mode
        offline_flag = get_steam_offline_flag()
        wants_offline = get_wants_offline(self._steam_dir)
        if offline_flag == 1 or wants_offline:
            note = "Currently OFFLINE"
        elif offline_flag == 0:
            note = "Online (go offline before trip)"
        else:
            note = "Available (go offline before trip)"
        self._set(4, "info", note)
        results.append(True)

        # Verdict
        critical_ok = results[0] and results[1] and results[2]
        def _verdict():
            if not self.winfo_exists(): return
            if critical_ok:
                self._verdict.config(
                    text="✅ Ready for offline play!\nLaunch Steam in offline mode before going offline.",
                    fg=GREEN)
            else:
                issues = []
                if not results[0]: issues.append("install the game first")
                if not results[1]: issues.append("game may not support sharing offline")
                if not results[2]: issues.append("launch online at least once first")
                self._verdict.config(
                    text=f"⚠ Not offline ready: {', '.join(issues)}.", fg=YELLOW)
        try:
            self.after(0, _verdict)
        except (tk.TclError, RuntimeError):
            pass


# ═══════════════════════════════════════════════════════════════════════════
# SECTION 10 — FAMILY COOLDOWN SIMULATOR
# ═══════════════════════════════════════════════════════════════════════════

class FamilyCooldownDialog(tk.Toplevel):
    """Simulates cooldown impact before making Steam Family changes."""

    COOLDOWN_DAYS = 365
    MAX_MEMBERS   = 6

    def __init__(self, parent, steam_dir: Optional[Path] = None):
        super().__init__(parent)
        self._steam_dir = Path(steam_dir) if steam_dir else get_steam_dir()
        self.title("Steam Family Cooldown Simulator")
        self.configure(bg=BG_DARK)
        self.resizable(False, False)
        _center_over_parent(self, parent, 520, 540)
        _safe_grab(self)
        self._build()

    @staticmethod
    def _add_one_year(d: datetime) -> datetime:
        """Return d + 1 calendar year, handling Feb 29 → Feb 28 safely."""
        try:
            return d.replace(year=d.year + 1)
        except ValueError:
            # d is Feb 29 and the next year is not a leap year.
            return d.replace(year=d.year + 1, day=28)

    def _build(self):
        hdr = tk.Frame(self, bg=BG_PANEL, height=52)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="⏰  Family Cooldown Simulator",
                 bg=BG_PANEL, fg=TEXT_MAIN, font=F_HEAD).pack(side="left", padx=16, pady=12)
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        tk.Label(self,
                 text="Preview the impact of Steam Family changes before you make them.\n"
                      "Steam enforces strict 1-year cooldowns — understand them first.",
                 bg=BG_DARK, fg=TEXT_DIM, font=F_SMALL,
                 wraplength=480, justify="left").pack(padx=16, pady=(10,4), anchor="w")

        # Rules card
        rules_frame = tk.Frame(self, bg=BG_CARD)
        rules_frame.pack(fill="x", padx=16, pady=(0,8))
        rules = [
            ("👥 Max members",       "6 people per family"),
            ("⏳ Account cooldown",  "1 year after leaving before joining another family"),
            ("🪑 Slot cooldown",     "1 year before a vacated slot can be refilled"),
            ("🔄 Rejoin exception",  "You CAN rejoin YOUR previous family (if < 6 members)"),
            ("🏠 Household check",   "Steam verifies same household activity"),
            ("👶 Children",          "Children cannot leave — must be removed by an adult"),
        ]
        for label, value in rules:
            row = tk.Frame(rules_frame, bg=BG_CARD)
            row.pack(fill="x", padx=10, pady=1)
            tk.Label(row, text=label, bg=BG_CARD, fg=TEXT_DIM,
                     font=("Segoe UI", 8), width=24, anchor="w").pack(side="left")
            tk.Label(row, text=value, bg=BG_CARD, fg=TEXT_MAIN,
                     font=("Segoe UI", 8), anchor="w").pack(side="left", pady=3)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(2,0))
        tk.Label(self, text="SIMULATE A CHANGE:", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=16, pady=(8,4))

        btn_row = tk.Frame(self, bg=BG_DARK)
        btn_row.pack(fill="x", padx=16)
        scenarios = [
            ("Remove a member",  self._sim_remove),
            ("Leave the family", self._sim_leave),
            ("Add a new member", self._sim_add),
        ]
        for label, cmd in scenarios:
            tk.Button(btn_row, text=label, bg=BG_CARD, fg=TEXT_MAIN,
                      font=F_SMALL, relief="flat", bd=0, cursor="hand2",
                      command=cmd).pack(side="left", ipady=6, ipadx=10, padx=(0,6))

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(10,0))
        tk.Label(self, text="SIMULATION RESULT:", bg=BG_DARK, fg=TEXT_DIM,
                 font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=16, pady=(6,2))
        self._result = scrolledtext.ScrolledText(
            self, bg=BG_PANEL, fg=TEXT_MAIN, font=F_MONO,
            relief="flat", bd=0, state="disabled", height=9)
        self._result.pack(fill="both", expand=True, padx=16, pady=(0,4))

        tk.Button(self, text="Close", bg=BG_CARD, fg=TEXT_DIM, font=F_SMALL,
                  relief="flat", bd=0, cursor="hand2",
                  command=self.destroy).pack(pady=8, ipady=5, ipadx=20)

        self._write_result(self._current_state_text())

    def _write_result(self, text: str):
        self._result.config(state="normal")
        self._result.delete("1.0","end")
        self._result.insert("end", text)
        self._result.config(state="disabled")

    def _current_state_text(self) -> str:
        users = get_all_steam_users(self._steam_dir)
        lines = ["STEAM ACCOUNTS ON THIS MACHINE\n" + "─"*42]
        for u in users[:8]:
            name = u["personaname"] or u["accountname"] or u["steamid64"]
            ts   = u["timestamp"]
            try:
                last = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "unknown"
            except (OSError, OverflowError, ValueError):
                last = "unknown"
            tag  = " ← active" if u["mostrecent"] else ""
            lines.append(f"  {name:<28} last login: {last}{tag}")
        lines += ["", "Select a scenario above to simulate the cooldown impact."]
        return "\n".join(lines)

    def _sim_remove(self):
        today   = datetime.now()
        unlock  = self._add_one_year(today)
        lines = [
            "SCENARIO: Remove a member from your family\n" + "─"*48,
            "",
            f"  Action date:            {today.strftime('%B %d, %Y')}",
            f"  Removed member cooldown ends:  {unlock.strftime('%B %d, %Y')}",
            f"  Slot available for new member: {unlock.strftime('%B %d, %Y')} (earliest)",
            "",
            "⚠  IMMEDIATE EFFECTS:",
            "  • Removed member loses ALL shared game access instantly",
            "  • Removed member CANNOT join another family for 1 year",
            f"  • Their slot is locked until {unlock.strftime('%B %d, %Y')}",
            "  • Their saves/achievements are NOT deleted",
            "",
            "✅  SAFE IF:",
            "  • They have personal copies of all games they need",
            "  • You don't plan to add someone new for 1 year",
            "  • They understand the 1-year cooldown before telling them",
        ]
        self._write_result("\n".join(lines))

    def _sim_leave(self):
        today  = datetime.now()
        unlock = self._add_one_year(today)
        lines = [
            "SCENARIO: You leave your current family\n" + "─"*46,
            "",
            f"  Action date:                  {today.strftime('%B %d, %Y')}",
            f"  Can join new family after:    {unlock.strftime('%B %d, %Y')}",
            f"  Can create new family after:  {unlock.strftime('%B %d, %Y')}",
            "",
            "🔄  EXCEPTION — You can STILL rejoin your PREVIOUS family:",
            "  • If your old family has fewer than 6 members, you can",
            "    rejoin it WITHOUT waiting the full year.",
            "",
            "⚠  IMMEDIATE EFFECTS:",
            "  • You lose ALL shared game access instantly",
            "  • You CANNOT join or create any other family for 1 year",
            "  • Your vacated slot is locked for 1 year for replacements",
            "",
            "✅  SAFE IF:",
            "  • You own personal copies of everything important",
            "  • You plan to rejoin the same family (use the exception)",
            "  • You are OK without shared access for up to 1 year",
        ]
        self._write_result("\n".join(lines))

    def _sim_add(self):
        lines = [
            "SCENARIO: Add a new member to your family\n" + "─"*48,
            "",
            "✅  REQUIREMENTS (Steam checks all of these):",
            "  • Your family has fewer than 6 members currently",
            "  • The person is NOT already in another family",
            "  • They are NOT in a 1-year post-leave cooldown",
            "  • Steam detects 'same household' activity between you",
            "    (same network/IP at some recent point helps)",
            "",
            "🏠  HOUSEHOLD CHECK:",
            "  • Steam looks at account activity patterns",
            "  • Logging into Steam on the same WiFi can satisfy this",
            "  • Family members living separately may face additional",
            "    verification steps from Steam Support",
            "",
            "⏳  AFTER ADDING:",
            "  • New member gets full family library access immediately",
            "  • If you later remove them, their slot is locked 1 year",
            "  • They cannot be in another family while in yours",
            "",
            "ℹ  If Steam blocks the invite, go to:",
            "  Steam → Family → Manage Family → Add Member",
            "  and read the exact error message for the specific reason.",
        ]
        self._write_result("\n".join(lines))
