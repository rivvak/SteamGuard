"""
Secure local session cache.
Stores the last server-issued session token so the app can start
offline for up to 24 hours without hitting the server.

The cache entry is HMAC-signed with a machine-unique key so it
cannot be tampered with or copied to another machine.
"""

import json
import hmac
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from auth.hwid import get_hwid

_CACHE_DIR  = Path(os.environ.get("APPDATA", "")) / "SteamGuard"
_CACHE_FILE = _CACHE_DIR / ".session"   # hidden-ish name

# Machine-unique signing key = SHA-256 of HWID + static salt
_SALT = b"steamguard-cache-v1"


def _cache_key() -> bytes:
    return hashlib.sha256(_SALT + get_hwid().encode()).digest()


def _sign(data: dict) -> str:
    payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hmac.new(_cache_key(), payload.encode(), hashlib.sha256).hexdigest()


def save_session(session_token: str, token_expires_iso: str,
                 key: str, discord_user_id: str):
    """Persist a valid session to disk."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "session_token":    session_token,
        "token_expires":    token_expires_iso,
        "key":              key,
        "discord_user_id":  discord_user_id,
    }
    data["sig"] = _sign(data)
    try:
        _CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def load_session() -> dict | None:
    """
    Load and validate cached session.
    Returns the session dict if valid and not expired, else None.
    """
    try:
        if not _CACHE_FILE.exists():
            return None
        raw  = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        sig  = raw.pop("sig", "")
        # Verify integrity
        if not hmac.compare_digest(_sign(raw), sig):
            return None   # tampered
        # Check token expiry
        expires = datetime.fromisoformat(raw["token_expires"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now > expires:
            return None   # expired
        return raw
    except Exception:
        return None


def clear_session():
    try:
        _CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def session_needs_refresh() -> bool:
    """
    True if there is no valid cache OR the token expires within 2 hours
    (trigger a background re-verify ahead of the hard deadline).
    """
    session = load_session()
    if session is None:
        return True
    try:
        expires = datetime.fromisoformat(session["token_expires"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        from datetime import timedelta
        return datetime.now(timezone.utc) > expires - timedelta(hours=2)
    except Exception:
        return True
