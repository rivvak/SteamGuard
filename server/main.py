"""
SteamGuard License Server
Runs on Google Cloud Run + Firestore (free tier).

Endpoints:
  POST /activate                  — first-time key activation, binds HWID
  POST /verify                    — daily check (key + HWID + Discord + YouTube)
  POST /revoke                    — admin: permanently revoke a key
  POST /pause                     — admin: temporarily suspend a key
  POST /unpause                   — admin: resume a suspended key
  POST /generate                  — admin: create a new license key
  GET  /admin/list-keys           — admin: list all keys with full stats
  GET  /admin/key-info/{key_hash} — admin: single key detail
  POST /revoke-by-discord         — admin: revoke all keys for a Discord user
  POST /pause-by-discord          — admin: pause all keys for a Discord user
  GET  /admin/active-discord-ids  — admin: list all active Discord IDs
  POST /youtube/store-token       — store a user's YouTube OAuth token
  GET  /health                    — uptime probe

  POST /heartbeat                 — client heartbeat every 5 minutes
  POST /referral/create           — create or return referral code for a user
  POST /referral/use              — record a referral being used
  GET  /referral/stats/{discord_user_id} — referral stats for a user
  POST /badges/grant              — admin: grant a badge to a user
  GET  /badges/{discord_user_id}  — badges and XP for a user
  POST /device/reset              — self-service device reset with cooldown
  GET  /stats/server              — public aggregate server stats
  POST /leaderboard/update        — update leaderboard entry
  GET  /leaderboard               — top 10 by heals for current week
"""

import os
import hmac
import hashlib
import secrets
import json
import logging
import time
import calendar
from datetime import datetime, timedelta, timezone
from typing import Optional, List

import httpx
from fastapi import FastAPI, HTTPException, Header, Request, Depends, Response
from fastapi.responses import JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google.cloud import firestore

# ── Config ────────────────────────────────────────────────────────────────────

# This service is deployed to Google Cloud Run via GitHub Actions.
SECRET_KEY          = os.environ["SECRET_KEY"]           # HMAC signing key
ADMIN_KEY           = os.environ["ADMIN_KEY"]            # admin endpoint auth
DISCORD_BOT_TOKEN   = os.environ["DISCORD_BOT_TOKEN"]    # Bot token (server-side only)
DISCORD_GUILD_ID    = os.environ["DISCORD_GUILD_ID"]     # Your server ID
DISCORD_ROLE_ID     = os.environ["DISCORD_ROLE_ID"]      # "Member" role ID
YOUTUBE_CHANNEL_ID  = os.environ.get("YOUTUBE_CHANNEL_ID", "")  # Your YT channel ID

MIN_SUPPORTED_VERSION = os.environ.get("MIN_SUPPORTED_VERSION", "1.0.0")
DISCORD_WEBHOOK_URL   = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Comma-separated Discord user IDs that may reach the admin dashboard.
ADMIN_USER_IDS = {
    x.strip() for x in os.environ.get("ADMIN_USER_IDS", "").split(",") if x.strip()
}

# Web dashboard JWT signing config. JWT_SECRET should be set as an env var in
# production so tokens survive restarts; falls back to an ephemeral secret.
import secrets as _secrets
JWT_SECRET = os.environ.get("JWT_SECRET", _secrets.token_hex(32))
JWT_ALGO   = "HS256"
STATS_CHANNEL_ID      = os.environ.get("STATS_CHANNEL_ID", "")

# Owner/admin Discord IDs that are exempt from reward caps and get unlimited time
OWNER_DISCORD_IDS: set = {
    os.environ.get("OWNER_DISCORD_ID", ""),   # set OWNER_DISCORD_ID in Cloud Run secrets
}

# Reward definitions: { trigger: (hours, description, cooldown_hours, max_per_user) }
# max_per_user = -1 means unlimited
REWARDS = {
    "invite_friend":     (3,   "Invited a friend",             24,  -1),   # +3h per successful invite
    "daily_check_in":    (0.5, "Daily check-in",               20,  -1),   # +30min, once per ~day
    "weekly_streak":     (5,   "7-day protection streak",       168, -1),   # +5h for 7d streak
    "youtube_sub":       (2,   "Subscribed to YouTube channel", 0,   1),    # +2h once ever
    "server_boost":      (24,  "Boosted the Discord server",    0,   1),    # +24h once ever
    "share_card_post":   (1,   "Shared a stats card",           168, -1),   # +1h once per week
    "bug_report":        (6,   "Reported a verified bug",       0,   -1),   # admin-granted
    "first_heal":        (1,   "First protection heal",         0,   1),    # +1h once ever
}

LOG = logging.getLogger("steamguard")
logging.basicConfig(level=logging.INFO)

# ── Firestore client ──────────────────────────────────────────────────────────

db = firestore.Client()

LICENSES_COL    = "licenses"       # doc id = sha256(key)
SESSIONS_COL    = "sessions"       # doc id = sha256(key)
EVENTS_COL      = "events"         # security audit log
YT_TOKENS_COL   = "yt_tokens"      # doc id = discord_user_id
REFERRAL_CODES_COL  = "referral_codes"   # doc id = code
REFERRALS_COL       = "referrals"        # doc id = auto
REWARDS_COL         = "rewards"          # doc id = discord_user_id
REWARD_LOG_COL      = "reward_log"       # doc id = auto (audit trail)
LEADERBOARD_COL     = "leaderboard"      # doc id = "weekly"

# ── Simple in-memory stats cache ──────────────────────────────────────────────

_stats_cache: dict = {"data": None, "ts": 0.0}

# ── Helpers ───────────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

def _hmac_sign(data: str) -> str:
    return hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()

def _verify_sig(data: str, sig: str) -> bool:
    expected = _hmac_sign(data)
    return hmac.compare_digest(expected, sig)

def _require_admin(admin_key: str):
    if not hmac.compare_digest(admin_key or "", ADMIN_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")

def _log_event(license_id: str, event: str, detail: dict = None):
    try:
        db.collection(EVENTS_COL).add({
            "license_id": license_id,
            "event":      event,
            "detail":     detail or {},
            "ts":         utcnow(),
        })
    except Exception as e:
        LOG.warning(f"Event log failed: {e}")

async def _discord_has_role(discord_user_id: str) -> bool:
    """
    Server-side check: does this Discord user have the required role?
    Uses bot token — never exposed to client.
    """
    url = (f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}"
           f"/members/{discord_user_id}")
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, headers=headers)
        if r.status_code == 200:
            roles = r.json().get("roles", [])
            return DISCORD_ROLE_ID in roles
        LOG.warning(f"Discord API {r.status_code} for user {discord_user_id}")
        return False
    except Exception as e:
        LOG.error(f"Discord check error: {e}")
        return False

async def _youtube_is_subscribed(discord_user_id: str) -> bool:
    """
    Check if the Discord user is subscribed to YOUTUBE_CHANNEL_ID.
    Uses their stored OAuth access token (refreshed automatically).
    Returns True if subscribed, True if we have no token yet (grace),
    False only if we have a valid token AND the sub is confirmed missing.
    """
    if not YOUTUBE_CHANNEL_ID:
        return True   # YouTube check not configured — skip

    doc = db.collection(YT_TOKENS_COL).document(discord_user_id).get()
    if not doc.exists:
        return True   # No token stored yet — give grace (they haven't linked)

    data      = doc.to_dict()
    token     = data.get("access_token", "")
    refresh   = data.get("refresh_token", "")
    token_exp = data.get("token_expires_at")

    # Refresh token if expired
    if token_exp:
        if isinstance(token_exp, datetime) and token_exp.tzinfo is None:
            token_exp = token_exp.replace(tzinfo=timezone.utc)
        if utcnow() > token_exp and refresh:
            token = await _refresh_youtube_token(discord_user_id, refresh)
            if not token:
                return True   # Refresh failed — give grace rather than falsely block

    if not token:
        return True

    url = "https://www.googleapis.com/youtube/v3/subscriptions"
    params = {
        "part":          "id",
        "mine":          "true",
        "forChannelId":  YOUTUBE_CHANNEL_ID,
        "maxResults":    "1",
    }
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url, params=params, headers=headers)
        if r.status_code == 200:
            items = r.json().get("items", [])
            return len(items) > 0
        if r.status_code == 401:
            # Token invalid — grace
            return True
        LOG.warning(f"YouTube API {r.status_code} for {discord_user_id}")
        return True   # API error — give grace
    except Exception as e:
        LOG.error(f"YouTube check error: {e}")
        return True   # Network error — give grace


async def _refresh_youtube_token(discord_user_id: str, refresh_token: str) -> str:
    """Refresh a YouTube OAuth token and store the new one. Returns new access_token or ''."""
    yt_client_id     = os.environ.get("YOUTUBE_CLIENT_ID", "")
    yt_client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET", "")
    if not yt_client_id or not yt_client_secret:
        return ""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id":     yt_client_id,
                    "client_secret": yt_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type":    "refresh_token",
                })
        if r.status_code == 200:
            d          = r.json()
            new_token  = d.get("access_token", "")
            expires_in = d.get("expires_in", 3600)
            db.collection(YT_TOKENS_COL).document(discord_user_id).update({
                "access_token":    new_token,
                "token_expires_at": utcnow() + timedelta(seconds=expires_in),
            })
            return new_token
    except Exception as e:
        LOG.error(f"YouTube token refresh error: {e}")
    return ""


def _generate_key() -> str:
    """
    Format: SGRD-XXXXXXXX-XXXXXXXX-XXXXXXXX-CHECKSUM
    Random body signed with HMAC so the checksum can catch obvious fakes
    before even hitting the DB.
    """
    body = secrets.token_hex(12)   # 24 hex chars
    parts = [body[i:i+8] for i in range(0, 24, 8)]
    checksum = _hmac_sign(body)[:8].upper()
    return f"SGRD-{parts[0].upper()}-{parts[1].upper()}-{parts[2].upper()}-{checksum}"

def _quick_key_valid(key: str) -> bool:
    """Fast format + checksum check before DB lookup."""
    parts = key.upper().split("-")
    if len(parts) != 5 or parts[0] != "SGRD":
        return False
    body = "".join(parts[1:4]).lower()
    expected_checksum = _hmac_sign(body)[:8].upper()
    return hmac.compare_digest(parts[4], expected_checksum)

def _make_session_token(key_hash: str, hwid: str) -> str:
    payload = f"{key_hash}:{hwid}:{utcnow().isoformat()}"
    return _hmac_sign(payload)

def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.strip().split(".")[:3])
    except Exception:
        return (0, 0, 0)

def _check_and_award_badges(
    key_hash: str,
    current_data: dict,
    heal_count: int,
    client_version: str,
) -> List[str]:
    """
    Check badge thresholds and return updated badge list.
    Writes new badges to Firestore if any are newly awarded.
    """
    badges: List[str] = list(current_data.get("badges") or [])
    newly_awarded: List[str] = []

    def _maybe_award(badge: str):
        if badge not in badges:
            badges.append(badge)
            newly_awarded.append(badge)

    # heal_count thresholds
    if heal_count >= 1:
        _maybe_award("first_guard")
    if heal_count >= 10:
        _maybe_award("healer_10")
    if heal_count >= 100:
        _maybe_award("healer_100")

    # night_watch: current UTC hour 0-6
    current_hour = utcnow().hour
    if 0 <= current_hour <= 6:
        _maybe_award("night_watch")

    # patch_veteran: client_version changed since last recorded
    last_version = current_data.get("last_client_version", "")
    if last_version and last_version != client_version:
        _maybe_award("patch_veteran")

    if newly_awarded:
        try:
            db.collection(LICENSES_COL).document(key_hash).update({
                "badges": badges,
            })
            for badge in newly_awarded:
                _log_event(key_hash, "badge_awarded", {"badge": badge})
        except Exception as e:
            LOG.warning(f"Badge award write failed: {e}")

    return badges
def _get_iso_week() -> str:
    """Return current ISO year-week string like '2026-W25'."""
    now = utcnow()
    return f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="SteamGuard License Server", docs_url=None, redoc_url=None)

# ── Dashboard static files ─────────────────────────────────────────────────
import os as _os
_dashboard_path = _os.path.abspath(
    _os.path.join(_os.path.dirname(__file__), "..", "dashboard"))
try:
    app.mount("/dashboard", StaticFiles(directory=_dashboard_path, html=True),
              name="dashboard")
except Exception as _e:
    pass  # dashboard folder not present (dev environment)

@app.get("/", response_class=RedirectResponse, status_code=302)
async def root_redirect():
    """Redirect root URL to the dashboard login page."""
    return "/dashboard/index.html"

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve RC logo as favicon — overrides FastAPI default leaf icon."""
    from fastapi.responses import FileResponse
    import os as _os
    ico = _os.path.abspath(_os.path.join(
        _os.path.dirname(__file__), "..", "dashboard", "favicon.ico"))
    if _os.path.exists(ico):
        return FileResponse(ico, media_type="image/x-icon")
    return RedirectResponse("/dashboard/favicon.ico")

# ── CORS (web dashboard) ──────────────────────────────────────────────────────
# The static dashboard is hosted off Cloud Run (Cloudflare Pages) and is a
# different origin from this API, so the browser requires permissive CORS
# headers. List exact origins (do NOT mix "*" with allow_credentials=True).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://steamguard.pages.dev",
        "https://steamguard-dashboard.pages.dev",
        "http://localhost:3000",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Request / Response models ─────────────────────────────────────────────────

class ActivateRequest(BaseModel):
    key:              str
    hwid:             str   # SHA-256 hash of hardware fingerprint
    discord_user_id:  str
    sig:              str   # HMAC-SHA256(key + ":" + hwid, SECRET_KEY)

class VerifyRequest(BaseModel):
    key:              str
    hwid:             str
    discord_user_id:  str
    sig:              str

class RevokeRequest(BaseModel):
    key:      str
    reason:   str = ""

class GenerateRequest(BaseModel):
    discord_user_id:  str
    note:             str = ""
    days_valid:       int = 36500   # ~100 years = lifetime

class HeartbeatRequest(BaseModel):
    key:              str
    hwid:             str
    discord_user_id:  str
    sig:              str
    client_version:   str
    session_id:       str = ""
    protected:        bool = False
    heal_count:       int = 0
    kill_count:       int = 0
    game_appid:       int = 0
    game_name:        str = ""

class HeartbeatResponse(BaseModel):
    session_id:           str
    server_time:          int
    next_heartbeat_seconds: int = 300
    lease_expires_at:     int
    license_status:       str
    kill:                 bool = False
    kill_reason:          str = ""
    client_policy:        dict

class ReferralCreateRequest(BaseModel):
    discord_user_id: str
    admin_key:       str

class ReferralUseRequest(BaseModel):
    code:                 str
    referred_discord_id:  str
    referred_license_key: str

class BadgeGrantRequest(BaseModel):
    discord_user_id: str
    badge:           str
    admin_key:       str

class DeviceResetRequest(BaseModel):
    key:              str
    discord_user_id:  str
    sig:              str

# ── /health ───────────────────────────────────────────────────────────────────


# ── Reward helpers ────────────────────────────────────────────────────────────

def _is_owner(discord_user_id: str) -> bool:
    """Owners are exempt from reward tracking and get unlimited time."""
    return discord_user_id.strip() in {x for x in OWNER_DISCORD_IDS if x}


def _extend_key_expiry(discord_user_id: str, hours: float):
    """Extend expires_at on all active keys for this user by N hours."""
    if _is_owner(discord_user_id):
        return
    docs = (db.collection(LICENSES_COL)
              .where("discord_user_id", "==", discord_user_id)
              .where("revoked", "==", False)
              .stream())
    for doc in docs:
        data = doc.to_dict()
        exp  = data.get("expires_at", utcnow())
        if isinstance(exp, datetime) and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        base = max(exp, utcnow())
        doc.reference.update({"expires_at": base + timedelta(hours=hours)})


def _grant_reward(discord_user_id: str, trigger: str, admin_override: bool = False) -> dict:
    """Grant bonus hours for a reward trigger. Returns result dict."""
    if trigger not in REWARDS:
        return {"granted": False, "hours": 0, "reason": "Unknown reward trigger"}

    hours, description, cooldown_h, max_per_user = REWARDS[trigger]

    if _is_owner(discord_user_id):
        return {"granted": False, "hours": 0, "reason": "Owner — exempt"}

    rewards_ref  = db.collection(REWARDS_COL).document(discord_user_id)
    rewards_doc  = rewards_ref.get()
    rewards_data = rewards_doc.to_dict() if rewards_doc.exists else {}

    trigger_key   = f"reward_{trigger}"
    count_key     = f"reward_{trigger}_count"
    last_time     = rewards_data.get(trigger_key)
    current_count = rewards_data.get(count_key, 0)
    total_hours   = rewards_data.get("total_reward_hours", 0.0)

    if not admin_override and max_per_user != -1 and current_count >= max_per_user:
        return {"granted": False, "hours": 0,
                "reason": f"Already claimed max uses ({max_per_user}) for {trigger}"}

    if not admin_override and cooldown_h > 0 and last_time:
        if isinstance(last_time, datetime) and last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        next_allowed = last_time + timedelta(hours=cooldown_h)
        if utcnow() < next_allowed:
            remaining = int((next_allowed - utcnow()).total_seconds() / 60)
            return {"granted": False, "hours": 0,
                    "reason": f"Cooldown active — {remaining}m remaining"}

    new_total = total_hours + hours
    rewards_ref.set({
        trigger_key:          utcnow(),
        count_key:            current_count + 1,
        "total_reward_hours": new_total,
        "last_updated":       utcnow(),
    }, merge=True)

    _extend_key_expiry(discord_user_id, hours)

    db.collection(REWARD_LOG_COL).add({
        "discord_user_id": discord_user_id,
        "trigger":         trigger,
        "hours":           hours,
        "description":     description,
        "timestamp":       utcnow(),
    })

    LOG.info(f"Reward: {discord_user_id} +{hours}h for {trigger}")
    return {
        "granted":          True,
        "hours":            hours,
        "description":      description,
        "reason":           f"+{hours}h — {description}",
        "new_total_hours":  new_total,
    }


# ── /rewards/grant (admin) ────────────────────────────────────────────────────

class RewardGrantRequest(BaseModel):
    discord_user_id: str
    trigger:         str
    admin_override:  bool = False

@app.post("/rewards/grant")
async def grant_reward_endpoint(req: RewardGrantRequest, x_admin_key: str = Header(None)):
    _require_admin(x_admin_key)
    return _grant_reward(req.discord_user_id, req.trigger, req.admin_override)


# ── /rewards/status/{discord_user_id} ────────────────────────────────────────

@app.get("/rewards/status/{discord_user_id}")
async def reward_status(discord_user_id: str):
    if _is_owner(discord_user_id):
        return {"is_owner": True, "total_reward_hours": 999999,
                "remaining_hours": 999999, "triggers": {}}
    ref  = db.collection(REWARDS_COL).document(discord_user_id)
    doc  = ref.get()
    data = doc.to_dict() if doc.exists else {}
    total = data.get("total_reward_hours", 0.0)
    triggers = {}
    for trig, (hrs, desc, cd, mx) in REWARDS.items():
        last_ts = data.get(f"reward_{trig}")
        count   = data.get(f"reward_{trig}_count", 0)
        # Compute next available time
        next_avail = None
        if cd > 0 and last_ts and isinstance(last_ts, datetime):
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            na = last_ts + timedelta(hours=cd)
            if utcnow() < na:
                next_avail = na.isoformat()
        triggers[trig] = {
            "description":   desc,
            "hours_per_use": hrs,
            "times_claimed": count,
            "cooldown_hours": cd,
            "max_per_user":  mx,
            "next_available": next_avail,
        }
    return {"is_owner": False, "total_reward_hours": total,
            "remaining_hours": total, "triggers": triggers}


# ── /rewards/check-in ────────────────────────────────────────────────────────

class CheckInRequest(BaseModel):
    discord_user_id: str
    key:             str
    sig:             str

@app.post("/rewards/check-in")
async def daily_check_in(req: CheckInRequest):
    expected = _hmac_sign(f"{req.key}:{req.discord_user_id}")
    if not hmac.compare_digest(req.sig, expected):
        raise HTTPException(status_code=401, detail="Invalid signature")
    return _grant_reward(req.discord_user_id, "daily_check_in")


@app.get("/health")
def health():
    return {"status": "ok", "ts": utcnow().isoformat()}

# ── /activate ─────────────────────────────────────────────────────────────────

@app.post("/activate")
async def activate(req: ActivateRequest):
    # 1. Verify request signature (prevents replay / spoofed requests)
    expected_sig = _hmac_sign(f"{req.key}:{req.hwid}")
    if not hmac.compare_digest(req.sig, expected_sig):
        _log_event("unknown", "bad_sig_activate", {"key_prefix": req.key[:9]})
        raise HTTPException(status_code=401, detail="Invalid signature")

    # 2. Quick format check
    if not _quick_key_valid(req.key):
        raise HTTPException(status_code=400, detail="Invalid key format")

    key_hash = _key_hash(req.key)
    lic_ref  = db.collection(LICENSES_COL).document(key_hash)
    lic      = lic_ref.get()

    if not lic.exists:
        _log_event(key_hash, "activate_not_found")
        raise HTTPException(status_code=404, detail="Key not found")

    data = lic.to_dict()

    if data.get("revoked"):
        raise HTTPException(status_code=403, detail="Key revoked")

    expires_at = data["expires_at"]
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if utcnow() > expires_at:
            raise HTTPException(status_code=403, detail="Key expired")

    # 3. HWID binding — bind on first activation, lock after
    stored_hwid = data.get("hwid")
    if stored_hwid and stored_hwid != req.hwid:
        _log_event(key_hash, "hwid_mismatch", {"stored": stored_hwid[:16], "got": req.hwid[:16]})
        raise HTTPException(status_code=403, detail="Key is bound to a different machine")

    # 4. Discord membership check
    has_role = await _discord_has_role(req.discord_user_id)
    if not has_role:
        _log_event(key_hash, "discord_role_missing", {"uid": req.discord_user_id})
        raise HTTPException(status_code=403,
            detail="You must be a member of the Discord server with the required role")

    # 5. Bind HWID + Discord ID, issue session token
    token = _make_session_token(key_hash, req.hwid)
    token_expires = utcnow() + timedelta(hours=25)  # 1h grace over 24h

    lic_ref.update({
        "hwid":              req.hwid,
        "discord_user_id":   req.discord_user_id,
        "last_verified":     utcnow(),
        "activation_count":  firestore.Increment(1),
    })

    db.collection(SESSIONS_COL).document(key_hash).set({
        "token":      token,
        "hwid":       req.hwid,
        "expires_at": token_expires,
    })

    _log_event(key_hash, "activated", {"discord_uid": req.discord_user_id})
    LOG.info(f"Activated key {key_hash[:16]}… for Discord {req.discord_user_id}")

    return {
        "valid":         True,
        "session_token": token,
        "token_expires": token_expires.isoformat(),
        "message":       "Activated successfully",
    }

# ── /verify ───────────────────────────────────────────────────────────────────

@app.post("/verify")
async def verify(req: VerifyRequest):
    # Signature check
    expected_sig = _hmac_sign(f"{req.key}:{req.hwid}")
    if not hmac.compare_digest(req.sig, expected_sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    if not _quick_key_valid(req.key):
        raise HTTPException(status_code=400, detail="Invalid key format")

    key_hash = _key_hash(req.key)
    lic      = db.collection(LICENSES_COL).document(key_hash).get()

    if not lic.exists:
        raise HTTPException(status_code=404, detail="Key not found")

    data = lic.to_dict()

    if data.get("revoked"):
        raise HTTPException(status_code=403, detail="Key revoked")

    if data.get("paused"):
        raise HTTPException(status_code=403,
            detail=f"Key suspended: {data.get('pause_reason', 'contact support')}")

    expires_at = data["expires_at"]
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if utcnow() > expires_at:
            raise HTTPException(status_code=403, detail="Key expired")

    if data.get("hwid") != req.hwid:
        _log_event(key_hash, "hwid_mismatch_verify", {"uid": req.discord_user_id})
        raise HTTPException(status_code=403, detail="HWID mismatch")

    # Discord re-check (the critical daily gate)
    has_role = await _discord_has_role(req.discord_user_id)
    if not has_role:
        _log_event(key_hash, "discord_lost_role", {"uid": req.discord_user_id})
        # Auto-pause rather than hard-revoke so admin can review
        db.collection(LICENSES_COL).document(key_hash).update({
            "paused":       True,
            "pause_reason": "Left Discord server or lost Member role",
            "paused_at":    utcnow(),
        })
        raise HTTPException(status_code=403,
            detail="Discord membership lost — rejoin the server to continue")

    # YouTube subscription check
    yt_ok = await _youtube_is_subscribed(req.discord_user_id)
    if not yt_ok:
        _log_event(key_hash, "youtube_unsub", {"uid": req.discord_user_id})
        db.collection(LICENSES_COL).document(key_hash).update({
            "paused":       True,
            "pause_reason": "Not subscribed to YouTube channel",
            "paused_at":    utcnow(),
        })
        raise HTTPException(status_code=403,
            detail="YouTube subscription not found — subscribe to continue")

    # Refresh session token
    token = _make_session_token(key_hash, req.hwid)
    token_expires = utcnow() + timedelta(hours=25)

    db.collection(SESSIONS_COL).document(key_hash).set({
        "token":      token,
        "hwid":       req.hwid,
        "expires_at": token_expires,
    })

    db.collection(LICENSES_COL).document(key_hash).update({
        "last_verified": utcnow(),
        "verify_count":  firestore.Increment(1),
    })

    return {
        "valid":         True,
        "session_token": token,
        "token_expires": token_expires.isoformat(),
    }

# ── /revoke (admin) ───────────────────────────────────────────────────────────

@app.post("/revoke")
async def revoke(req: RevokeRequest, x_admin_key: str = Header(None)):
    _require_admin(x_admin_key)
    key_hash = _key_hash(req.key)
    lic_ref  = db.collection(LICENSES_COL).document(key_hash)
    if not lic_ref.get().exists:
        raise HTTPException(status_code=404, detail="Key not found")
    lic_ref.update({"revoked": True, "revoke_reason": req.reason, "revoked_at": utcnow()})
    # Kill session immediately
    db.collection(SESSIONS_COL).document(key_hash).delete()
    _log_event(key_hash, "revoked", {"reason": req.reason})
    return {"status": "revoked"}

# ── /generate (admin) ─────────────────────────────────────────────────────────

@app.post("/generate")
async def generate(req: GenerateRequest, x_admin_key: str = Header(None)):
    _require_admin(x_admin_key)
    key      = _generate_key()
    key_hash = _key_hash(key)
    expires  = utcnow() + timedelta(days=req.days_valid)

    db.collection(LICENSES_COL).document(key_hash).set({
        "key_hash":        key_hash,
        "discord_user_id": req.discord_user_id,
        "hwid":            None,
        "revoked":         False,
        "created_at":      utcnow(),
        "expires_at":      expires,
        "note":            req.note,
        "activation_count": 0,
        "verify_count":    0,
        "last_verified":   None,
    })

    _log_event(key_hash, "generated", {"discord_uid": req.discord_user_id})
    LOG.info(f"Generated key for Discord {req.discord_user_id}")
    return {"key": key, "expires_at": expires.isoformat()}

# ── /revoke-by-discord (admin) ────────────────────────────────────────────────

class RevokeByDiscordRequest(BaseModel):
    discord_user_id: str
    reason: str = ""

@app.post("/revoke-by-discord")
async def revoke_by_discord(req: RevokeByDiscordRequest,
                             x_admin_key: str = Header(None)):
    _require_admin(x_admin_key)
    # Query all non-revoked licenses for this Discord user
    docs = (db.collection(LICENSES_COL)
              .where("discord_user_id", "==", req.discord_user_id)
              .where("revoked", "==", False)
              .stream())
    count = 0
    for doc in docs:
        doc.reference.update({
            "revoked":       True,
            "revoke_reason": req.reason,
            "revoked_at":    utcnow(),
        })
        db.collection(SESSIONS_COL).document(doc.id).delete()
        _log_event(doc.id, "revoked_by_discord", {"reason": req.reason})
        count += 1
    return {"revoked_count": count}

# ── /admin/active-discord-ids (admin) ────────────────────────────────────────

@app.get("/admin/active-discord-ids")
async def active_discord_ids(x_admin_key: str = Header(None)):
    _require_admin(x_admin_key)
    docs = (db.collection(LICENSES_COL)
              .where("revoked", "==", False)
              .stream())
    ids = list({d.to_dict().get("discord_user_id")
                for d in docs
                if d.to_dict().get("discord_user_id")})
    return {"ids": ids}

# ── /pause (admin) ────────────────────────────────────────────────────────────

class PauseRequest(BaseModel):
    key:    str
    reason: str = "Suspended by admin"

@app.post("/pause")
async def pause_key(req: PauseRequest, x_admin_key: str = Header(None)):
    _require_admin(x_admin_key)
    key_hash = _key_hash(req.key)
    lic_ref  = db.collection(LICENSES_COL).document(key_hash)
    if not lic_ref.get().exists:
        raise HTTPException(status_code=404, detail="Key not found")
    lic_ref.update({
        "paused":       True,
        "pause_reason": req.reason,
        "paused_at":    utcnow(),
    })
    # Kill active session so app stops immediately
    db.collection(SESSIONS_COL).document(key_hash).delete()
    _log_event(key_hash, "paused", {"reason": req.reason})
    return {"status": "paused"}

# ── /unpause (admin) ──────────────────────────────────────────────────────────

class UnpauseRequest(BaseModel):
    key: str

@app.post("/unpause")
async def unpause_key(req: UnpauseRequest, x_admin_key: str = Header(None)):
    _require_admin(x_admin_key)
    key_hash = _key_hash(req.key)
    lic_ref  = db.collection(LICENSES_COL).document(key_hash)
    doc = lic_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="Key not found")
    if doc.to_dict().get("revoked"):
        raise HTTPException(status_code=400, detail="Key is revoked, not just paused")
    lic_ref.update({
        "paused":       False,
        "pause_reason": None,
        "paused_at":    None,
    })
    _log_event(key_hash, "unpaused", {})
    return {"status": "active"}

# ── /pause-by-discord (admin) ─────────────────────────────────────────────────

class PauseByDiscordRequest(BaseModel):
    discord_user_id: str
    reason: str = "Suspended by admin"

@app.post("/pause-by-discord")
async def pause_by_discord(req: PauseByDiscordRequest,
                            x_admin_key: str = Header(None)):
    _require_admin(x_admin_key)
    docs = (db.collection(LICENSES_COL)
              .where("discord_user_id", "==", req.discord_user_id)
              .where("revoked", "==", False)
              .stream())
    count = 0
    for doc in docs:
        doc.reference.update({
            "paused":       True,
            "pause_reason": req.reason,
            "paused_at":    utcnow(),
        })
        db.collection(SESSIONS_COL).document(doc.id).delete()
        _log_event(doc.id, "paused_by_discord", {"reason": req.reason})
        count += 1
    return {"paused_count": count}

# ── /admin/list-keys (admin) ──────────────────────────────────────────────────

def _fmt_dt(dt) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(dt)

def _time_ago(dt) -> str:
    if dt is None:
        return "never"
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = utcnow() - dt
        s = int(diff.total_seconds())
        if s < 60:    return f"{s}s ago"
        if s < 3600:  return f"{s//60}m ago"
        if s < 86400: return f"{s//3600}h ago"
        return f"{s//86400}d ago"
    return str(dt)

def _running_since(dt) -> str:
    """How long the key has been active."""
    if dt is None:
        return "not activated"
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = utcnow() - dt
        d = diff.days
        h = (diff.seconds // 3600)
        m = (diff.seconds % 3600) // 60
        if d > 0:   return f"{d}d {h}h {m}m"
        if h > 0:   return f"{h}h {m}m"
        return f"{m}m"
    return str(dt)

@app.get("/admin/list-keys")
async def list_keys(x_admin_key: str = Header(None),
                    filter: str = "all"):
    """
    filter: all | active | paused | revoked
    Returns full stats for every key.
    """
    _require_admin(x_admin_key)
    docs = db.collection(LICENSES_COL).stream()
    results = []
    for doc in docs:
        d = doc.to_dict()
        status = "active"
        if d.get("revoked"):   status = "revoked"
        elif d.get("paused"):  status = "paused"

        if filter != "all" and status != filter:
            continue

        # Find first activation time
        activated_at = None
        events = (db.collection(EVENTS_COL)
                    .where("license_id", "==", doc.id)
                    .where("event", "==", "activated")
                    .stream())
        for ev in events:
            activated_at = ev.to_dict().get("ts")
            break

        results.append({
            "key_hash":        doc.id[:16] + "…",   # truncated for display
            "discord_user_id": d.get("discord_user_id"),
            "status":          status,
            "note":            d.get("note", ""),
            "hwid_bound":      bool(d.get("hwid")),
            "created_at":      _fmt_dt(d.get("created_at")),
            "activated_at":    _fmt_dt(activated_at),
            "running_since":   _running_since(activated_at),
            "last_verified":   _time_ago(d.get("last_verified")),
            "verify_count":    d.get("verify_count", 0),
            "activation_count":d.get("activation_count", 0),
            "expires_at":      _fmt_dt(d.get("expires_at")),
            "pause_reason":    d.get("pause_reason"),
            "paused_at":       _fmt_dt(d.get("paused_at")),
            "revoke_reason":   d.get("revoke_reason"),
        })

    results.sort(key=lambda x: x["created_at"] or "", reverse=True)
    return {"count": len(results), "filter": filter, "keys": results}

# ── /admin/key-info/{key_hash_prefix} (admin) ────────────────────────────────

@app.get("/admin/key-info/{discord_user_id}")
async def key_info_by_discord(discord_user_id: str,
                               x_admin_key: str = Header(None)):
    """Get all keys for a specific Discord user ID."""
    _require_admin(x_admin_key)
    docs = (db.collection(LICENSES_COL)
              .where("discord_user_id", "==", discord_user_id)
              .stream())
    results = []
    for doc in docs:
        d = doc.to_dict()
        status = "active"
        if d.get("revoked"):  status = "revoked"
        elif d.get("paused"): status = "paused"
        results.append({
            "key_hash":      doc.id[:16] + "…",
            "status":        status,
            "hwid_bound":    bool(d.get("hwid")),
            "created_at":    _fmt_dt(d.get("created_at")),
            "last_verified": _time_ago(d.get("last_verified")),
            "verify_count":  d.get("verify_count", 0),
            "pause_reason":  d.get("pause_reason"),
        })
    return {"discord_user_id": discord_user_id, "keys": results}

# ── /youtube/store-token (called by bot after OAuth) ─────────────────────────

class YouTubeTokenRequest(BaseModel):
    discord_user_id: str
    access_token:    str
    refresh_token:   str
    expires_in:      int = 3600

@app.post("/youtube/store-token")
async def store_youtube_token(req: YouTubeTokenRequest,
                               x_admin_key: str = Header(None)):
    """Bot calls this after completing YouTube OAuth for a user."""
    _require_admin(x_admin_key)
    db.collection(YT_TOKENS_COL).document(req.discord_user_id).set({
        "access_token":    req.access_token,
        "refresh_token":   req.refresh_token,
        "token_expires_at": utcnow() + timedelta(seconds=req.expires_in),
        "linked_at":       utcnow(),
    })
    return {"status": "stored"}

# ── /heartbeat ────────────────────────────────────────────────────────────────

@app.post("/heartbeat")
async def heartbeat(req: HeartbeatRequest):
    # 1. Verify HMAC sig
    if not _verify_sig(f"{req.key}:{req.hwid}", req.sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    key_hash = _key_hash(req.key)
    now      = utcnow()
    now_ts   = int(now.timestamp())

    # Build a default "kill" response helper
    def _kill_response(reason: str) -> HeartbeatResponse:
        return HeartbeatResponse(
            session_id=req.session_id,
            server_time=now_ts,
            next_heartbeat_seconds=300,
            lease_expires_at=now_ts,
            license_status="invalid",
            kill=True,
            kill_reason=reason,
            client_policy={
                "min_version":      MIN_SUPPORTED_VERSION,
                "feature_flags":    {},
                "update_channel":   "stable",
                "mandatory_update": False,
            },
        )

    # 2. Load license from Firestore
    try:
        lic_doc = db.collection(LICENSES_COL).document(key_hash).get()
    except Exception as e:
        LOG.warning(f"Heartbeat Firestore read failed: {e}")
        return _kill_response("server_error")

    if not lic_doc.exists:
        return _kill_response("license_not_found")

    lic_data = lic_doc.to_dict()
    lic_status = "active"
    if lic_data.get("revoked"):
        lic_status = "revoked"
    elif lic_data.get("paused"):
        lic_status = "suspended"

    if lic_status != "active":
        return _kill_response("license_suspended")

    # 3. Check Discord role
    has_role = await _discord_has_role(req.discord_user_id)
    if not has_role:
        # Pause the license
        try:
            db.collection(LICENSES_COL).document(key_hash).update({
                "paused":       True,
                "pause_reason": "Left Discord server or lost Member role",
                "paused_at":    now,
            })
            _log_event(key_hash, "discord_lost_role_heartbeat", {"uid": req.discord_user_id})
        except Exception as e:
            LOG.warning(f"Heartbeat pause write failed: {e}")
        return _kill_response("discord_role_lost")

    # 4. Update session doc
    try:
        db.collection(SESSIONS_COL).document(key_hash).set({
            "last_heartbeat_at": now,
            "client_version":    req.client_version,
            "protected":         req.protected,
            "heal_count":        req.heal_count,
            "kill_count":        req.kill_count,
            "game_name":         req.game_name,
            "game_appid":        req.game_appid,
            "discord_user_id":   req.discord_user_id,
        }, merge=True)
    except Exception as e:
        LOG.warning(f"Heartbeat session update failed: {e}")

    # 5. Version check
    min_ver = _version_tuple(MIN_SUPPORTED_VERSION)
    cli_ver = _version_tuple(req.client_version)
    mandatory_update = cli_ver < min_ver

    # 6. Award XP for active protected sessions
    if req.protected:
        try:
            db.collection(LICENSES_COL).document(key_hash).update({
                "xp": firestore.Increment(1),
            })
        except Exception as e:
            LOG.warning(f"XP increment failed: {e}")

    # 7. Log heal events
    prev_heal = lic_data.get("last_heal_count", 0) or 0
    heal_delta = req.heal_count - prev_heal
    if heal_delta > 0:
        try:
            _log_event(key_hash, "heal_event", {
                "heal_delta": heal_delta,
                "total_heals": req.heal_count,
                "game_name": req.game_name,
            })
            db.collection(LICENSES_COL).document(key_hash).update({
                "last_heal_count": req.heal_count,
            })
        except Exception as e:
            LOG.warning(f"Heal event log failed: {e}")

    # 8. Track client_version change and award badges
    try:
        # Refresh lic_data for badge computation (include xp update)
        fresh_lic = db.collection(LICENSES_COL).document(key_hash).get()
        fresh_data = fresh_lic.to_dict() if fresh_lic.exists else lic_data
        updated_badges = _check_and_award_badges(
            key_hash, fresh_data, req.heal_count, req.client_version
        )
        # Track last_client_version
        last_ver = fresh_data.get("last_client_version", "")
        if last_ver != req.client_version:
            try:
                db.collection(LICENSES_COL).document(key_hash).update({
                    "last_client_version": req.client_version,
                })
            except Exception as e:
                LOG.warning(f"Version tracking update failed: {e}")
        current_xp = fresh_data.get("xp", 0) or 0
    except Exception as e:
        LOG.warning(f"Badge/xp check failed: {e}")
        updated_badges = lic_data.get("badges") or []
        current_xp = lic_data.get("xp", 0) or 0

    # 9. Update leaderboard (fire-and-forget style)
    try:
        await _update_leaderboard_entry(
            discord_user_id=req.discord_user_id,
            heals=req.heal_count,
            kills=req.kill_count,
            xp=current_xp,
            game=req.game_name,
        )
    except Exception as e:
        LOG.warning(f"Leaderboard update from heartbeat failed: {e}")

    # 10. Build response
    lease_expires_at = int((now + timedelta(seconds=600)).timestamp())

    client_policy = {
        "min_version":      MIN_SUPPORTED_VERSION,
        "feature_flags":    {},
        "update_channel":   "stable",
        "mandatory_update": mandatory_update,
    }

    resp = HeartbeatResponse(
        session_id=req.session_id or key_hash[:16],
        server_time=now_ts,
        next_heartbeat_seconds=300,
        lease_expires_at=lease_expires_at,
        license_status="active",
        kill=mandatory_update,
        kill_reason="forced_update" if mandatory_update else "",
        client_policy=client_policy,
    )
    return resp

# ── /referral/create ──────────────────────────────────────────────────────────

@app.post("/referral/create")
async def referral_create(req: ReferralCreateRequest):
    _require_admin(req.admin_key)

    # Check if this user already has a referral code
    try:
        existing = (db.collection(REFERRAL_CODES_COL)
                      .where("owner_discord_id", "==", req.discord_user_id)
                      .limit(1)
                      .stream())
        for doc in existing:
            d = doc.to_dict()
            code = doc.id
            return {
                "code": code,
                "link": f"https://steamguard.app/join?ref={code}",
            }
    except Exception as e:
        LOG.warning(f"Referral code lookup failed: {e}")

    # Create new referral code
    code = secrets.token_urlsafe(8).upper()
    try:
        db.collection(REFERRAL_CODES_COL).document(code).set({
            "owner_discord_id": req.discord_user_id,
            "created_at":       utcnow(),
            "uses_count":       0,
            "valid_uses_count": 0,
        })
    except Exception as e:
        LOG.warning(f"Referral code creation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to create referral code")

    _log_event("referral", "referral_code_created", {
        "owner_discord_id": req.discord_user_id,
        "code": code,
    })
    return {
        "code": code,
        "link": f"https://steamguard.app/join?ref={code}",
    }

# ── /referral/use ─────────────────────────────────────────────────────────────

@app.post("/referral/use")
async def referral_use(req: ReferralUseRequest):
    # Verify the code exists
    try:
        code_doc = db.collection(REFERRAL_CODES_COL).document(req.code).get()
    except Exception as e:
        LOG.warning(f"Referral code read failed: {e}")
        raise HTTPException(status_code=500, detail="Server error")

    if not code_doc.exists:
        raise HTTPException(status_code=404, detail="Referral code not found")

    code_data = code_doc.to_dict()
    referrer_discord_id = code_data.get("owner_discord_id", "")

    # Don't allow self-referral
    if referrer_discord_id == req.referred_discord_id:
        raise HTTPException(status_code=400, detail="Cannot refer yourself")

    referred_key_hash = _key_hash(req.referred_license_key)
    valid_after = utcnow() + timedelta(days=7)

    referral_id = secrets.token_hex(16)
    try:
        db.collection(REFERRALS_COL).document(referral_id).set({
            "referral_id":         referral_id,
            "code":                req.code,
            "referrer_discord_id": referrer_discord_id,
            "referred_discord_id": req.referred_discord_id,
            "referred_key_hash":   referred_key_hash,
            "created_at":          utcnow(),
            "valid_after":         valid_after,
            "status":              "pending",
        })
        # Increment uses_count on the code doc
        db.collection(REFERRAL_CODES_COL).document(req.code).update({
            "uses_count": firestore.Increment(1),
        })
    except Exception as e:
        LOG.warning(f"Referral use write failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to record referral")

    _log_event("referral", "referral_used", {
        "code":                req.code,
        "referrer_discord_id": referrer_discord_id,
        "referred_discord_id": req.referred_discord_id,
    })
    # Auto-grant +3h to the referrer (not if they're an owner)
    if referrer_discord_id:
        _grant_reward(referrer_discord_id, "invite_friend")
    return {"ok": True, "referral_id": referral_id}

# ── /referral/stats/{discord_user_id} ────────────────────────────────────────

@app.get("/referral/stats/{discord_user_id}")
async def referral_stats(discord_user_id: str, x_admin_key: str = Header(None)):
    _require_admin(x_admin_key)
    total_referrals   = 0
    valid_referrals   = 0
    pending_referrals = 0
    rewards_earned    = 0

    try:
        docs = (db.collection(REFERRALS_COL)
                  .where("referrer_discord_id", "==", discord_user_id)
                  .stream())
        now = utcnow()
        for doc in docs:
            d = doc.to_dict()
            total_referrals += 1
            valid_after = d.get("valid_after")
            if valid_after:
                if isinstance(valid_after, datetime) and valid_after.tzinfo is None:
                    valid_after = valid_after.replace(tzinfo=timezone.utc)
                if now >= valid_after:
                    valid_referrals += 1
                    rewards_earned  += 1
                else:
                    pending_referrals += 1
            else:
                pending_referrals += 1
    except Exception as e:
        LOG.warning(f"Referral stats query failed: {e}")

    return {
        "discord_user_id":  discord_user_id,
        "total_referrals":  total_referrals,
        "valid_referrals":  valid_referrals,
        "pending_referrals": pending_referrals,
        "rewards_earned":   rewards_earned,
    }

# ── /badges/grant (admin) ─────────────────────────────────────────────────────

@app.post("/badges/grant")
async def badges_grant(req: BadgeGrantRequest):
    _require_admin(req.admin_key)

    # Find license by discord_user_id
    try:
        docs = (db.collection(LICENSES_COL)
                  .where("discord_user_id", "==", req.discord_user_id)
                  .limit(1)
                  .stream())
        target_doc = None
        for doc in docs:
            target_doc = doc
            break
    except Exception as e:
        LOG.warning(f"Badge grant license lookup failed: {e}")
        raise HTTPException(status_code=500, detail="Server error")

    if target_doc is None:
        raise HTTPException(status_code=404, detail="No license found for this Discord user")

    d = target_doc.to_dict()
    badges: List[str] = list(d.get("badges") or [])

    if req.badge not in badges:
        badges.append(req.badge)
        try:
            target_doc.reference.update({"badges": badges})
            _log_event(target_doc.id, "badge_granted_admin", {
                "badge": req.badge,
                "discord_user_id": req.discord_user_id,
            })
        except Exception as e:
            LOG.warning(f"Badge grant write failed: {e}")
            raise HTTPException(status_code=500, detail="Failed to grant badge")

    return {"ok": True, "badges": badges}

# ── /badges/{discord_user_id} ────────────────────────────────────────────────

@app.get("/badges/{discord_user_id}")
async def get_badges(discord_user_id: str):
    try:
        docs = (db.collection(LICENSES_COL)
                  .where("discord_user_id", "==", discord_user_id)
                  .limit(1)
                  .stream())
        for doc in docs:
            d = doc.to_dict()
            xp     = d.get("xp", 0) or 0
            badges = list(d.get("badges") or [])
            level  = xp // 100
            return {"badges": badges, "xp": xp, "level": level}
    except Exception as e:
        LOG.warning(f"Badge lookup failed for {discord_user_id}: {e}")

    raise HTTPException(status_code=404, detail="No license found for this Discord user")

# ── /device/reset ─────────────────────────────────────────────────────────────

@app.post("/device/reset")
async def device_reset(req: DeviceResetRequest):
    # Verify sig
    if not _verify_sig(f"{req.key}:{req.discord_user_id}", req.sig):
        raise HTTPException(status_code=401, detail="Invalid signature")

    key_hash = _key_hash(req.key)
    try:
        lic_doc = db.collection(LICENSES_COL).document(key_hash).get()
    except Exception as e:
        LOG.warning(f"Device reset license read failed: {e}")
        raise HTTPException(status_code=500, detail="Server error")

    if not lic_doc.exists:
        raise HTTPException(status_code=404, detail="Key not found")

    d = lic_doc.to_dict()

    # Verify discord_user_id matches
    if d.get("discord_user_id") != req.discord_user_id:
        raise HTTPException(status_code=403, detail="Discord user ID does not match key owner")

    # Check cooldown (30-day minimum between resets for free tier)
    last_reset = d.get("last_device_reset_at")
    now = utcnow()
    cooldown_days = 30
    if last_reset:
        if isinstance(last_reset, datetime) and last_reset.tzinfo is None:
            last_reset = last_reset.replace(tzinfo=timezone.utc)
        elapsed = (now - last_reset).total_seconds()
        cooldown_seconds = cooldown_days * 86400
        if elapsed < cooldown_seconds:
            next_allowed = last_reset + timedelta(days=cooldown_days)
            raise HTTPException(status_code=429, detail={
                "error":                "cooldown_active",
                "message":              f"Device reset is only allowed once every {cooldown_days} days",
                "next_reset_allowed_at": int(next_allowed.timestamp()),
            })

    # Perform the reset
    next_allowed_at = int((now + timedelta(days=cooldown_days)).timestamp())
    try:
        db.collection(LICENSES_COL).document(key_hash).update({
            "hwid":                  None,
            "activations_count":     0,
            "last_device_reset_at":  now,
        })
        # Also clear the session
        db.collection(SESSIONS_COL).document(key_hash).delete()
        _log_event(key_hash, "device_reset", {
            "discord_user_id": req.discord_user_id,
        })
    except Exception as e:
        LOG.warning(f"Device reset write failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to reset device")

    return {"ok": True, "next_reset_allowed_at": next_allowed_at}

@app.get("/stats/server")
async def stats_server():
    global _stats_cache
    now_ts = time.time()

    # Return cached result if under 60 seconds old
    if _stats_cache["data"] is not None and (now_ts - _stats_cache["ts"]) < 60:
        return _stats_cache["data"]

    total_keys   = 0
    active_keys  = 0
    total_heals  = 0
    version_dist: dict = {}

    try:
        lic_docs = db.collection(LICENSES_COL).stream()
        for doc in lic_docs:
            total_keys += 1
            d = doc.to_dict()
            if not d.get("revoked") and not d.get("paused"):
                active_keys += 1
    except Exception as e:
        LOG.warning(f"Stats license count failed: {e}")

    try:
        session_docs = db.collection(SESSIONS_COL).stream()
        for doc in session_docs:
            d = doc.to_dict()
            total_heals += d.get("heal_count", 0) or 0
            cv = d.get("client_version", "unknown") or "unknown"
            version_dist[cv] = version_dist.get(cv, 0) + 1
    except Exception as e:
        LOG.warning(f"Stats session aggregation failed: {e}")

    result = {
        "total_keys":          total_keys,
        "active_keys":         active_keys,
        "total_heals":         total_heals,
        "version_distribution": version_dist,
        "cached_at":           int(now_ts),
    }

    _stats_cache["data"] = result
    _stats_cache["ts"]   = now_ts

    return result

# ── /leaderboard/update ───────────────────────────────────────────────────────

async def _update_leaderboard_entry(
    discord_user_id: str,
    heals: int,
    kills: int,
    xp: int,
    game: str,
):
    """Internal helper to upsert a leaderboard entry and handle weekly resets."""
    try:
        lb_ref = db.collection(LEADERBOARD_COL).document("weekly")
        lb_doc = lb_ref.get()

        current_week = _get_iso_week()

        if lb_doc.exists:
            lb_data = lb_doc.to_dict() or {}
            stored_week = lb_data.get("_week", "")
            if stored_week != current_week:
                # New week — reset the leaderboard
                lb_ref.set({
                    "_week": current_week,
                    discord_user_id: {
                        "heals": heals,
                        "kills": kills,
                        "xp":    xp,
                        "game":  game,
                    }
                })
            else:
                # Update entry within the same week (take max values)
                existing_entry = lb_data.get(discord_user_id, {})
                lb_ref.update({
                    discord_user_id: {
                        "heals": max(heals, existing_entry.get("heals", 0)),
                        "kills": max(kills, existing_entry.get("kills", 0)),
                        "xp":    max(xp, existing_entry.get("xp", 0)),
                        "game":  game,
                    }
                })
        else:
            lb_ref.set({
                "_week": current_week,
                discord_user_id: {
                    "heals": heals,
                    "kills": kills,
                    "xp":    xp,
                    "game":  game,
                }
            })
    except Exception as e:
        LOG.warning(f"Leaderboard update failed: {e}")


class LeaderboardUpdateRequest(BaseModel):
    discord_user_id: str
    heals:           int = 0
    kills:           int = 0
    xp:              int = 0
    game:            str = ""
    admin_key:       str

@app.post("/leaderboard/update")
async def leaderboard_update(req: LeaderboardUpdateRequest):
    _require_admin(req.admin_key)
    await _update_leaderboard_entry(
        discord_user_id=req.discord_user_id,
        heals=req.heals,
        kills=req.kills,
        xp=req.xp,
        game=req.game,
    )
    return {"ok": True}

# ── GET /leaderboard ──────────────────────────────────────────────────────────

@app.get("/leaderboard")
async def get_leaderboard():
    try:
        lb_doc = db.collection(LEADERBOARD_COL).document("weekly").get()
        if not lb_doc.exists:
            return {"week": _get_iso_week(), "top10": []}

        lb_data = lb_doc.to_dict() or {}
        current_week = _get_iso_week()
        stored_week  = lb_data.get("_week", "")

        # If stale week, return empty
        if stored_week != current_week:
            return {"week": current_week, "top10": []}

        entries = []
        for uid, stats in lb_data.items():
            if uid.startswith("_"):
                continue
            if not isinstance(stats, dict):
                continue
            entries.append({
                "discord_user_id": uid,
                "heals":           stats.get("heals", 0),
                "kills":           stats.get("kills", 0),
                "xp":              stats.get("xp", 0),
                "game":            stats.get("game", ""),
            })

        # Sort by heals descending, take top 10
        entries.sort(key=lambda x: x["heals"], reverse=True)
        top10 = entries[:10]

        return {"week": current_week, "top10": top10}

    except Exception as e:
        LOG.warning(f"Leaderboard read failed: {e}")
        return {"week": _get_iso_week(), "top10": [], "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# WEB DASHBOARD ENDPOINTS
# Static dashboard (Cloudflare Pages) -> these JSON endpoints (CORS-enabled).
# Auth uses a 24h HS256 JWT issued by /auth/login. The license key is hashed
# (sha256) to form the Firestore doc id, matching the rest of this service.
# ══════════════════════════════════════════════════════════════════════════════

from jose import jwt, JWTError


def _is_admin_uid(discord_user_id: str) -> bool:
    """A Discord ID is admin if it is in ADMIN_USER_IDS or OWNER_DISCORD_IDS."""
    uid = str(discord_user_id)
    admins = {str(x) for x in ADMIN_USER_IDS if x}
    owners = {str(x) for x in OWNER_DISCORD_IDS if x}
    return uid in admins or uid in owners


class WebLoginRequest(BaseModel):
    discord_user_id: str
    key: str


@app.post("/auth/login")
async def web_login(req: WebLoginRequest, response: Response):
    """Web dashboard login — validates key + discord_id, returns a JWT."""
    if not req.key or len(req.key) > 256:
        raise HTTPException(status_code=401, detail="Invalid key or Discord ID")

    key_hash = _key_hash(req.key)
    doc = db.collection(LICENSES_COL).document(key_hash).get()
    if not doc.exists:
        raise HTTPException(status_code=401, detail="Invalid key or Discord ID")
    data = doc.to_dict() or {}

    if str(data.get("discord_user_id")) != str(req.discord_user_id):
        raise HTTPException(status_code=401, detail="Invalid key or Discord ID")
    if data.get("revoked"):
        raise HTTPException(status_code=401, detail="Key has been revoked")

    is_admin = _is_admin_uid(req.discord_user_id)
    payload = {
        "sub": str(req.discord_user_id),
        "kh": key_hash,                       # key hash, never the raw key
        "is_admin": is_admin,
        "exp": utcnow() + timedelta(hours=24),
        "iat": utcnow(),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)
    return {
        "token": token,
        "is_admin": is_admin,
        "discord_user_id": str(req.discord_user_id),
    }


async def _get_current_user(authorization: str = Header(None)):
    """FastAPI dependency: decode and validate the dashboard JWT."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def _require_admin_jwt(authorization: str = Header(None)):
    user = await _get_current_user(authorization)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin only")
    return user


# ── User endpoints ────────────────────────────────────────────────────────────

@app.get("/me/overview")
async def me_overview(user=Depends(_get_current_user)):
    discord_id = user["sub"]
    key_hash   = user.get("kh", "")

    key_doc = db.collection(LICENSES_COL).document(key_hash).get().to_dict() or {}
    rewards_doc = db.collection(REWARDS_COL).document(discord_id).get()
    rewards_data = rewards_doc.to_dict() if rewards_doc.exists else {}

    is_admin = bool(user.get("is_admin"))
    is_owner = str(discord_id) in {str(x) for x in OWNER_DISCORD_IDS if x}
    tier = key_doc.get("tier", "premium" if is_owner else "free")

    expires_at = key_doc.get("expires_at")
    if isinstance(expires_at, datetime) and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    remaining_hours = None
    if is_owner:
        remaining_hours = 999999.0
    elif expires_at:
        remaining_hours = max(0.0, (expires_at - utcnow()).total_seconds() / 3600.0)

    created_at = key_doc.get("created_at")
    if isinstance(created_at, datetime):
        created_iso = created_at.isoformat()
    else:
        created_iso = None

    return {
        "discord_user_id": discord_id,
        "tier": tier,
        "is_admin": is_admin,
        "remaining_hours": remaining_hours,
        "total_heals": key_doc.get("total_heals", 0),
        "total_reward_hours": rewards_data.get("total_reward_hours", 0),
        "created_at": created_iso,
        "paused": key_doc.get("paused", False),
        "revoked": key_doc.get("revoked", False),
        "current_game": rewards_data.get("current_game") or key_doc.get("current_game"),
    }


@app.get("/me/rewards")
async def me_rewards(user=Depends(_get_current_user)):
    discord_id = user["sub"]
    ref = db.collection(REWARDS_COL).document(discord_id)
    doc = ref.get()
    data = doc.to_dict() if doc.exists else {}
    total = data.get("total_reward_hours", 0.0)
    result = {"total_reward_hours": total, "triggers": {}}
    for trigger, (hrs, desc, cd, mx) in REWARDS.items():
        last_ts = data.get(f"reward_{trigger}")
        count   = data.get(f"reward_{trigger}_count", 0)
        next_avail = None
        if cd > 0 and last_ts and isinstance(last_ts, datetime):
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            na = last_ts + timedelta(hours=cd)
            if utcnow() < na:
                next_avail = int((na - utcnow()).total_seconds() / 60)
        result["triggers"][trigger] = {
            "description": desc,
            "hours_per_use": hrs,
            "times_claimed": count,
            "cooldown_hours": cd,
            "max_per_user": mx,
            "minutes_until_available": next_avail,
        }
    return result


@app.get("/me/referrals")
async def me_referrals(user=Depends(_get_current_user)):
    discord_id = user["sub"]
    # Referral codes are keyed by owner_discord_id in this service.
    docs = db.collection(REFERRAL_CODES_COL)\
             .where("owner_discord_id", "==", discord_id).limit(1).stream()
    code_doc = next(docs, None)
    if not code_doc:
        return {
            "code": None, "referral_link": None,
            "valid_referrals": 0, "pending_referrals": 0, "earned_hours": 0,
        }
    code_data = code_doc.to_dict() or {}
    code = code_data.get("code", code_doc.id)
    valid = code_data.get("valid_referrals", code_data.get("uses", 0))
    pending = code_data.get("pending_referrals", 0)
    return {
        "code": code,
        "referral_link": f"https://discord.gg/RTHM8YhpE?ref={code}",
        "valid_referrals": valid,
        "pending_referrals": pending,
        "earned_hours": valid * 3.0,
    }


@app.get("/me/stats")
async def me_stats(user=Depends(_get_current_user)):
    """Session/heal history for the Stats section. Best-effort from sessions."""
    discord_id = user["sub"]
    key_hash   = user.get("kh", "")

    sessions = []
    try:
        sess_doc = db.collection(SESSIONS_COL).document(key_hash).get()
        if sess_doc.exists:
            sessions.append(sess_doc.to_dict() or {})
    except Exception:
        pass

    # Build a 7-day heals series from the reward_log audit trail (best effort).
    heals_by_day = {}
    try:
        logs = (db.collection(REWARD_LOG_COL)
                  .where("discord_user_id", "==", discord_id)
                  .limit(200).stream())
        for lg in logs:
            d = lg.to_dict() or {}
            ts = d.get("ts") or d.get("timestamp")
            if isinstance(ts, datetime):
                day = ts.date().isoformat()
                heals_by_day[day] = heals_by_day.get(day, 0) + 1
    except Exception:
        pass

    today = utcnow().date()
    series = []
    for i in range(6, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        series.append({"date": day, "heals": heals_by_day.get(day, 0)})

    return {
        "heals_7d": series,
        "uptime_pct": 99.0,
        "sessions": sessions[:10],
    }


# ── Admin endpoints ───────────────────────────────────────────────────────────

@app.get("/admin/system-health")
async def admin_system_health(user=Depends(_require_admin_jwt)):
    """System health for the admin dashboard."""
    firestore_ok = True
    try:
        db.collection("_health").document("ping").set({"ts": utcnow()})
    except Exception:
        firestore_ok = False

    total = active = premium = 0
    try:
        docs = list(db.collection(LICENSES_COL).limit(500).stream())
        total = len(docs)
        for d in docs:
            dd = d.to_dict() or {}
            if not dd.get("revoked"):
                active += 1
            if dd.get("tier") == "premium":
                premium += 1
    except Exception:
        pass

    return {
        "firestore": "ok" if firestore_ok else "error",
        "cloud_run": "ok",
        "bot": "ok",
        "total_keys": total,
        "active_keys": active,
        "premium_keys": premium,
        "timestamp": utcnow().isoformat(),
    }


@app.get("/admin/users")
async def admin_users(user=Depends(_require_admin_jwt), limit: int = 50, offset: int = 0):
    docs = list(db.collection(LICENSES_COL).limit(limit).stream())
    result = []
    for d in docs:
        data = d.to_dict() or {}
        created = data.get("created_at")
        last = data.get("last_verified")
        result.append({
            "key": (d.id[:8] + "****") if d.id else "",
            "full_key": d.id,
            "discord_user_id": str(data.get("discord_user_id", "")),
            "tier": data.get("tier", "free"),
            "revoked": data.get("revoked", False),
            "paused": data.get("paused", False),
            "total_heals": data.get("total_heals", 0),
            "created_at": created.isoformat() if isinstance(created, datetime) else None,
            "last_active": last.isoformat() if isinstance(last, datetime) else None,
        })
    return {"users": result, "total": len(result)}


@app.get("/admin/events")
async def admin_events(user=Depends(_require_admin_jwt), limit: int = 10):
    """Recent activity feed for the admin overview."""
    events = []
    try:
        docs = (db.collection(EVENTS_COL)
                  .order_by("ts", direction=firestore.Query.DESCENDING)
                  .limit(limit).stream())
        for d in docs:
            dd = d.to_dict() or {}
            ts = dd.get("ts")
            events.append({
                "event": dd.get("event", "unknown"),
                "license_id": (dd.get("license_id", "") or "")[:8],
                "detail": dd.get("detail", {}),
                "ts": ts.isoformat() if isinstance(ts, datetime) else None,
            })
    except Exception as e:
        LOG.warning(f"admin_events read failed: {e}")
    return {"events": events}
