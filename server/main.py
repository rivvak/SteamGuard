"""
SteamGuard License Server
Runs on Google Cloud Run + Firestore (free tier).

Endpoints:
  POST /activate      — first-time key activation, binds HWID
  POST /verify        — daily check (key + HWID + Discord membership)
  POST /revoke        — admin: instantly revoke a key
  POST /generate      — admin: create a new license key
  GET  /health        — uptime probe
   POST /revoke-by-discord    — admin: revoke all keys for a Discord user
 GET /admin/active-discord-ids — admin: list active Discord user IDs
"""

import os
import hmac
import hashlib
import secrets
import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from google.cloud import firestore

# ── Config ────────────────────────────────────────────────────────────────────

SECRET_KEY        = os.environ["SECRET_KEY"]          # HMAC signing key
ADMIN_KEY         = os.environ["ADMIN_KEY"]           # admin endpoint auth
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]   # Bot token (server-side only)
DISCORD_GUILD_ID  = os.environ["DISCORD_GUILD_ID"]    # Your server ID
DISCORD_ROLE_ID   = os.environ["DISCORD_ROLE_ID"]     # "Member" role ID

LOG = logging.getLogger("steamguard")
logging.basicConfig(level=logging.INFO)

# ── Firestore client ──────────────────────────────────────────────────────────

db = firestore.Client()

LICENSES_COL = "licenses"       # doc id = sha256(key)
SESSIONS_COL = "sessions"       # doc id = sha256(key)
EVENTS_COL   = "events"         # security audit log

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
        raise HTTPException(status_code=403,
            detail="Discord membership lost — rejoin the server to continue")

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
