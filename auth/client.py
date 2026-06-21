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
from dataclasses import dataclass

from auth.hwid import get_hwid

# ─────────────────────────────────────────────────────────────────────────────
# FILL THESE IN after you deploy your Cloud Run service.
# _SERVER_URL:       your Cloud Run HTTPS URL
# _SERVER_CERT_HASH: SHA-256 fingerprint of your server's TLS cert
#                    run: python auth/get_cert_hash.py <your-url>
# _HMAC_SECRET:      must match SECRET_KEY on the server
# ─────────────────────────────────────────────────────────────────────────────

_SERVER_URL       = "https://steamguard-775181381055.us-central1.run.app"
_HMAC_SECRET      = "a9c5fb65fa6f744879ed15c50b07ab53e0e6b6a3960b20d28af9169770b2"
_SERVER_CERT_HASH: str | None = "9c2b9c67d1f15b8d7e29dedf913aa03304562810989d8d26c1010b11a42c3fa0"


# ── Request signing ───────────────────────────────────────────────────────────

def _sign(key: str, hwid: str) -> str:
    return hmac.new(_HMAC_SECRET.encode(),
                    f"{key}:{hwid}".encode(),
                    hashlib.sha256).hexdigest()


# ── HTTPS helper (with optional cert pinning) ─────────────────────────────────

def _post(path: str, payload: dict, timeout: int = 12) -> dict:
    url  = _SERVER_URL.rstrip("/") + path
    body = json.dumps(payload).encode()

    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2

    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent":   "SteamGuard/1.0"},
        method="POST")

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            # Optional cert pinning: compare DER hash after connection
            if _SERVER_CERT_HASH:
                der  = resp.fp.raw._sock.getpeercert(binary_form=True)
                got  = hashlib.sha256(der).hexdigest()
                if not hmac.compare_digest(got, _SERVER_CERT_HASH.lower()):
                    return {"error": "Certificate pinning failed — possible MITM"}
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail", str(e))
        except Exception:
            detail = str(e)
        return {"error": detail, "status": e.code}
    except Exception as e:
        return {"error": str(e)}


# ── Public API ────────────────────────────────────────────────────────────────

@dataclass
class AuthResult:
    ok:            bool
    session_token: str = ""
    token_expires: str = ""
    error:         str = ""


def activate(key: str, discord_user_id: str) -> AuthResult:
    """
    First-time activation. Binds HWID to key.
    Call once; use verify() for daily re-checks.
    """
    hwid = get_hwid()
    data = _post("/activate", {
        "key":             key,
        "hwid":            hwid,
        "discord_user_id": discord_user_id,
        "sig":             _sign(key, hwid),
    })
    if data.get("valid"):
        return AuthResult(
            ok=True,
            session_token=data["session_token"],
            token_expires=data["token_expires"])
    return AuthResult(ok=False, error=data.get("error", "Unknown error"))


def verify(key: str, discord_user_id: str) -> AuthResult:
    """
    Daily verification. Refreshes session token.
    If offline, caller should fall back to cache.load_session().
    """
    hwid = get_hwid()
    data = _post("/verify", {
        "key":             key,
        "hwid":            hwid,
        "discord_user_id": discord_user_id,
        "sig":             _sign(key, hwid),
    })
    if data.get("valid"):
        return AuthResult(
            ok=True,
            session_token=data["session_token"],
            token_expires=data["token_expires"])
    return AuthResult(ok=False, error=data.get("error", "Unknown error"))
