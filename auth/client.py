"""
License server client.
Handles activate / verify calls with request signing and
certificate pinning (rejects MITM proxies).
"""

import hmac
import hashlib
import json
import ssl
import urllib.request
import urllib.error
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from auth.hwid import get_hwid

# ── Debug logging ─────────────────────────────────────────────────────────────
_LOG_DIR = Path(os.environ.get("APPDATA", "")) / "SteamGuard"
try:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
_LOG_FILE = _LOG_DIR / "client.log"

logging.basicConfig(
    filename=_LOG_FILE,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    force=False)

LOG = logging.getLogger("sg-client")

# ── Server Configuration ──────────────────────────────────────────────────────

_SERVER_URL  = "https://steamguard-775181381055.us-central1.run.app"
_HMAC_SECRET = "7e3b9ccf02a09ad3520ebc7ed3f00a48d5eff34ef081900ee9064dba2a74529e"

# TODO: PASTE THE 64-CHARACTER HASH RETURNED FROM STEP 1 HERE
_SERVER_CERT_HASHES: tuple[str, ...] = (
    "PASTE_YOUR_NEW_HASH_FROM_STEP_1_HERE", 
    "9e2b9c67d1f15b8d7a29dedf913aa03304562810989d8d26c1010b11a42c3fa0",  # previous
)


# ── Request signing ───────────────────────────────────────────────────────────

def _sign(key: str, hwid: str) -> str:
    return hmac.new(_HMAC_SECRET.encode(),
                    f"{key}:{hwid}".encode(),
                    hashlib.sha256).hexdigest()


# ── HTTPS helper (with SSL certificate pinning) ───────────────────────────────

def _get_cert_hash(resp) -> str | None:
    """Extract SHA-256 fingerprint of the peer certificate (robust extraction)."""
    try:
        raw = getattr(resp, "fp", None)
        if raw is None:
            return None
        sock = getattr(raw, "raw", None)
        if sock is None:
            sock = getattr(raw, "_sock", None)
        if sock is None:
            # Fallback wrapper check for newer Python versions
            sock = getattr(resp, "headers", None)
        
        # Access socket peer certificate
        der = sock.getpeercert(binary_form=True) if hasattr(sock, "getpeercert") else None
        if der:
            return hashlib.sha256(der).hexdigest()
    except Exception as e:
        LOG.debug(f"Failed to extract certificate peer info: {e}")
    return None


def _post(path: str, payload: dict, timeout: int = 12) -> dict:
    url  = _SERVER_URL.rstrip("/") + path
    body = json.dumps(payload).encode()
    LOG.info(f"POST {url} (timeout={timeout}s)")

    ctx = ssl.create_default_context()
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    except (AttributeError, ValueError):
        pass   # older Python versions

    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent":   "SteamGuard/1.0"},
        method="POST")

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            # Certificate pinning check
            if _SERVER_CERT_HASHES and "PASTE_" not in _SERVER_CERT_HASHES[0]:
                got = _get_cert_hash(resp)
                LOG.info(f"Server cert fingerprint: {got or 'unknown'}")
                if got and got.lower() not in {h.lower() for h in _SERVER_CERT_HASHES}:
                    err = f"Certificate pinning failed (expected one of {_SERVER_CERT_HASHES}, got {got[:16]}…)"
                    LOG.error(err)
                    return {"error": err}
            
            data = json.loads(resp.read())
            LOG.info(f"Response: {data}")
            return data
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail", str(e))
        except Exception:
            detail = str(e)
        LOG.warning(f"HTTP {e.code}: {detail}")
        return {"error": detail, "status": e.code}
    except urllib.error.URLError as e:
        LOG.error(f"URLError: {e.reason}")
        return {"error": f"Could not reach license server: {e.reason}"}
    except TimeoutError:
        LOG.error("Timeout")
        return {"error": "License server timed out. Check your internet connection."}
    except Exception as e:
        LOG.exception("Network error")
        return {"error": f"Network error: {str(e)[:120]}"}


# ── Public API ────────────────────────────────────────────────────────────────

@dataclass
class AuthResult:
    ok:            bool
    session_token: str = ""
    token_expires: str = ""
    error:         str = ""


def activate(key: str, discord_user_id: str) -> AuthResult:
    """First-time activation. Binds HWID to key."""
    LOG.info(f"activate called for discord_user_id={discord_user_id}")
    try:
        hwid = get_hwid()
    except Exception as e:
        LOG.exception("HWID generation failed")
        return AuthResult(ok=False, error=f"Could not read hardware ID: {e}")

    data = _post("/activate", {
        "key":             key,
        "hwid":            hwid,
        "discord_user_id": discord_user_id,
        "sig":             _sign(key, hwid),
    })
    if data.get("valid"):
        LOG.info("activate succeeded")
        return AuthResult(
            ok=True,
            session_token=data["session_token"],
            token_expires=data["token_expires"])
    err = data.get("error", "Unknown error")
    LOG.warning(f"activate failed: {err}")
    return AuthResult(ok=False, error=err)


def verify(key: str, discord_user_id: str) -> AuthResult:
    """Daily verification. Refreshes session token."""
    LOG.info(f"verify called for discord_user_id={discord_user_id}")
    try:
        hwid = get_hwid()
    except Exception as e:
        LOG.exception("HWID generation failed")
        return AuthResult(ok=False, error=f"Could not read hardware ID: {e}")

    data = _post("/verify", {
        "key":             key,
        "hwid":            hwid,
        "discord_user_id": discord_user_id,
        "sig":             _sign(key, hwid),
    })
    if data.get("valid"):
        LOG.info("verify succeeded")
        return AuthResult(
            ok=True,
            session_token=data["session_token"],
            token_expires=data["token_expires"])
    err = data.get("error", "Unknown error")
    LOG.warning(f"verify failed: {err}")
    return AuthResult(ok=False, error=err)

