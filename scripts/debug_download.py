"""Debug the SteamGuard download exactly how the loader does it.

Simulates the loader's DownloadWorker end-to-end using the same token the
loader has cached, the same headers, and the same urllib call. Prints
verbose info at every stage so we can pinpoint exactly what fails.

Run:
    python scripts/debug_download.py
"""
from __future__ import annotations
import os
import ssl
import sys
import socket
import traceback
import urllib.request
import urllib.error
from pathlib import Path

# Add repo root to path so we can import loader helpers.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Match the loader's constants exactly.
LOADER_IDENTITY_TOKEN = "SteamGuard-Loader-Official-v2-Rivvak"
DEFAULT_SERVER_URL = os.environ.get("SG_SERVER_URL", "https://rivvak.app")
URL = f"{DEFAULT_SERVER_URL}/get-tool?tool=steamguard"
APPDATA = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "SteamGuard"
TOKEN_FILE = APPDATA / "loader_token.bin"
DEST = APPDATA / "tools" / "SteamGuard.exe"


def _try_load_cached_token() -> str | None:
    """Try to decrypt the loader's cached JWT the same way the loader does."""
    if not TOKEN_FILE.exists():
        return None
    try:
        raw = TOKEN_FILE.read_bytes()
    except Exception as e:
        print(f"  [!] Could not read token file: {e}")
        return None

    # Try DPAPI first (Windows), then XOR fallback.
    try:
        import win32crypt  # type: ignore
        try:
            decrypted, _ = win32crypt.CryptUnprotectData(raw, None, None, None, 0)
            return decrypted.decode("utf-8", errors="ignore")
        except Exception:
            pass
    except ImportError:
        pass

    # XOR fallback (matches _XOR_KEY in loader.py)
    key = b"RivvakSteamGuardLoader-v2-fallback-key-2026"
    try:
        decoded = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        # Heuristic: JWT starts with "ey"
        if decoded.startswith(b"ey"):
            return decoded.decode("utf-8", errors="ignore")
    except Exception:
        pass
    return None


def _print_section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main() -> int:
    _print_section("Environment")
    print(f"  Python:              {sys.version.split()[0]}")
    print(f"  Platform:            {sys.platform}")
    print(f"  SG_SERVER_URL:       {os.environ.get('SG_SERVER_URL', '(not set)')}")
    print(f"  Effective URL:       {URL}")
    print(f"  APPDATA:             {APPDATA}")
    print(f"  Token file exists:   {TOKEN_FILE.exists()}")
    print(f"  Dest exists:         {DEST.exists()}")
    if DEST.exists():
        print(f"  Dest size:           {DEST.stat().st_size} bytes")

    _print_section("Test 1: Identity header ONLY (no JWT)")
    headers1 = {
        "User-Agent": "SteamGuardLoader/2.0",
        "X-Loader-Identity": LOADER_IDENTITY_TOKEN,
    }
    _do_request(URL, headers1)

    _print_section("Test 2: Identity header + cached JWT (exactly what the loader sends)")
    token = _try_load_cached_token()
    if token:
        print(f"  Loaded cached JWT ({len(token)} chars): {token[:20]}...{token[-10:]}")
        headers2 = {
            "User-Agent": "SteamGuardLoader/2.0",
            "X-Loader-Identity": LOADER_IDENTITY_TOKEN,
            "Authorization": f"Bearer {token}",
        }
        _do_request(URL, headers2)
    else:
        print("  No cached JWT found — skipping. (This test needs you to be logged in.)")

    _print_section("Test 3: Malformed JWT (simulate corruption)")
    headers3 = {
        "User-Agent": "SteamGuardLoader/2.0",
        "X-Loader-Identity": LOADER_IDENTITY_TOKEN,
        "Authorization": "Bearer this.is.not.a.valid.jwt",
    }
    _do_request(URL, headers3)

    _print_section("Test 4: JWT ONLY (no identity header — simulate compromise)")
    if token:
        headers4 = {
            "User-Agent": "SteamGuardLoader/2.0",
            "Authorization": f"Bearer {token}",
        }
        _do_request(URL, headers4)

    _print_section("Test 5: Full download to check disk write path")
    if DEST.parent.exists() or True:
        try:
            DEST.parent.mkdir(parents=True, exist_ok=True)
            print(f"  Tools dir writable: {os.access(DEST.parent, os.W_OK)}")
            req = urllib.request.Request(URL, headers=headers1)
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                print(f"  Content-Length: {total}")
                downloaded = 0
                with open(DEST, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                print(f"  Wrote {downloaded} bytes to {DEST}")
                print(f"  Final size on disk: {DEST.stat().st_size}")
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}")
            traceback.print_exc()

    print("\nDone. Paste the ENTIRE output above back to the assistant.")
    return 0


def _do_request(url: str, headers: dict) -> None:
    """Send one HEAD-like GET (with body read) and report exact response."""
    print(f"  URL:     {url}")
    print(f"  Headers: {list(headers.keys())}")
    try:
        req = urllib.request.Request(url, headers=headers)
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            print(f"  ✅ HTTP {resp.status} {resp.reason}")
            print(f"     Content-Length: {resp.headers.get('Content-Length')}")
            print(f"     Redirected to:  {resp.geturl()}")
    except urllib.error.HTTPError as e:
        print(f"  ❌ HTTPError {e.code} {e.reason}")
        try:
            body = e.read().decode("utf-8", errors="ignore")[:200]
            print(f"     Body: {body}")
        except Exception:
            pass
    except urllib.error.URLError as e:
        print(f"  ❌ URLError: {e.reason}")
    except socket.timeout:
        print(f"  ❌ Timeout")
    except Exception as e:
        print(f"  ❌ {type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
