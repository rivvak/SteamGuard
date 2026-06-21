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
"""

import os
import hmac
import hashlib
import secrets
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
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

LOG = logging.getLogger("steamguard")
logging.basicConfig(level=logging.INFO)

# ── Firestore client ──────────────────────────────────────────────────────────

db = firestore.Client()

LICENSES_COL  = "licenses"       # doc id = sha256(key)
SESSIONS_COL  = "sessions"       # doc id = sha256(key)
EVENTS_COL    = "events"         # security audit log
YT_TOKENS_COL = "yt_tokens"      # doc id = discord_user_id

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

# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="SteamGuard License Server", docs_url=None, redoc_url=None)

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

# ── /health ───────────────────────────────────────────────────────────────────

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
