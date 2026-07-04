"""
SteamGuard auth diagnostic — run without the UI.
This prints exactly what the client sees when talking to the license server.
"""

import sys
import time
import traceback

sys.path.insert(0, r"C:\Users\xiq\Downloads\steamapp")

print("=" * 60)
print("SteamGuard Auth Diagnostic")
print("=" * 60)

# 1. HWID generation
print("\n[1/4] Generating HWID...")
try:
    from auth.hwid import get_hwid
    t0 = time.time()
    hwid = get_hwid()
    t1 = time.time()
    print(f"  HWID: {hwid[:16]}... ({len(hwid)} chars)")
    print(f"  Time: {t1 - t0:.2f}s")
except Exception as e:
    print(f"  FAILED: {e}")
    traceback.print_exc()
    sys.exit(1)

# 2. Raw server connectivity
print("\n[2/4] Checking raw HTTPS connectivity...")
try:
    import urllib.request
    import ssl
    url = "https://steamguard-775181381055.us-central1.run.app/health"
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "SteamGuard/1.0"}, method="GET")
    t0 = time.time()
    with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
        body = resp.read().decode()
        t1 = time.time()
        print(f"  Status: {resp.status}")
        print(f"  Body: {body}")
        print(f"  Time: {t1 - t0:.2f}s")
except Exception as e:
    print(f"  FAILED: {e}")
    traceback.print_exc()

# 3. Client _post with fake key
print("\n[3/4] Testing client _post (fake key, should fail gracefully)...")
try:
    from auth.client import activate, AuthResult
    t0 = time.time()
    result = activate("SGRD-00000000-00000000-00000000-00000000", "123456789012345678")
    t1 = time.time()
    print(f"  ok={result.ok}")
    print(f"  error={result.error}")
    print(f"  token={result.session_token[:20] if result.session_token else ''}")
    print(f"  Time: {t1 - t0:.2f}s")
except Exception as e:
    print(f"  FAILED: {e}")
    traceback.print_exc()

# 4. Client _post with real key format (if you have one)
print("\n[4/4] If you have a real key, test it below:")
try:
    key = input("Enter key (or press Enter to skip): ").strip()
except EOFError:
    key = ""
    print("  Non-interactive mode — skipped.")
if key:
    did = input("Enter Discord User ID: ").strip()
    try:
        from auth.client import activate
        t0 = time.time()
        result = activate(key, did)
        t1 = time.time()
        print(f"  ok={result.ok}")
        print(f"  error={result.error}")
        print(f"  Time: {t1 - t0:.2f}s")
    except Exception as e:
        print(f"  FAILED: {e}")
        traceback.print_exc()
else:
    print("  Skipped.")

print("\n" + "=" * 60)
print("Diagnostic complete")
print("=" * 60)
