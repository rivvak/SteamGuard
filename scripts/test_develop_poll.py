"""Smoke test for the non-blocking /develop session store (server side).

Exercises ai_hooks._Session + the /internal/develop kickoff +
/internal/develop/status/{sid} routes with the devbox /session call
(_post_session) stubbed out — no IAP tunnel, no gcloud, no Discord.

    python scripts/test_develop_poll.py

Covers:

  - kickoff returns {session_id, state:"starting"}
  - status transitions running -> done, propagating the stubbed PR payload
  - status transitions running -> error when _post_session raises (502)
  - unknown sid -> 404

This is the part of the /develop crash fix that has no Discord/cloud surface,
so it can run deterministically anywhere fastapi + httpx are installed.
"""

import os
import sys
import time

# Configure the env the ai_hooks module reads at import time. Must happen before
# the import — the module reads ADMIN_KEY/AI_DEVELOP_ENABLED at top level.
os.environ.setdefault("ADMIN_KEY", "testkey")
os.environ.setdefault("AI_DEVELOP_ENABLED", "true")
os.environ.setdefault("DISCORD_OWNER_ID", "1513150836472021074")

# Make `server.*` importable from the repo root (namespace package).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import asyncio  # noqa: E402

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.routes import ai_hooks  # noqa: E402

ADMIN = {"X-Admin-Key": "testkey"}


async def _ok(payload: dict) -> dict:
    await asyncio.sleep(0.15)
    return {
        "status": "pr_opened",
        "session_id": "stub",
        "pr_url": "https://github.com/rivvak/SteamGuard/pull/42",
        "files": ["server/bot/cogs/develop_cog.py"],
    }


async def _boom(payload: dict) -> dict:
    await asyncio.sleep(0.05)
    raise HTTPException(502, "devbox 500: agent exceeded the 20-minute time budget")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(ai_hooks.router)
    return app


def main() -> int:
    failures: list = []

    def check(cond: bool, msg: str) -> None:
        mark = "  ok  " if cond else " FAIL "
        print(f"{mark}{msg}")
        if not cond:
            failures.append(msg)

    with TestClient(_make_app()) as client:
        # 1) unknown sid -> 404
        r = client.get("/internal/develop/status/doesnotexist", headers=ADMIN)
        check(r.status_code == 404, f"unknown_session -> 404 (got {r.status_code})")

        # 2) success path
        ai_hooks._post_session = _ok
        r = client.post(
            "/internal/develop",
            headers=ADMIN,
            json={"discord_user_id": "999", "task": "test task", "deep": False},
        )
        check(r.status_code == 200, f"kickoff -> 200 (got {r.status_code}: {r.text[:200]})")
        data = r.json()
        check(data.get("state") == "starting", f"kickoff -> state=starting (got {data})")
        sid = data.get("session_id")
        check(bool(sid), "kickoff returns a session_id")

        # first status while the bg task is still running
        r = client.get(f"/internal/develop/status/{sid}", headers=ADMIN)
        st = r.json() if r.status_code == 200 else {}
        check(st.get("state") in ("running", "done"),
              f"first status running-or-done (got {r.status_code} {st})")

        # give the detached task a moment to resolve
        time.sleep(0.5)
        r = client.get(f"/internal/develop/status/{sid}", headers=ADMIN)
        st = r.json() if r.status_code == 200 else {}
        check(r.status_code == 200, f"final status -> 200 (got {r.status_code})")
        check(st.get("state") == "done", f"state -> done (got {st.get('state')})")
        check(st.get("status") == "pr_opened", f"status -> pr_opened (got {st.get('status')})")
        check(str(st.get("pr_url", "")).endswith("/pull/42"),
              f"pr_url propagated (got {st.get('pr_url')})")

        # 3) error path (devbox 502 / agent timeout)
        ai_hooks._post_session = _boom
        r = client.post(
            "/internal/develop",
            headers=ADMIN,
            json={"discord_user_id": "999", "task": "big task", "deep": False},
        )
        sid2 = r.json().get("session_id")
        time.sleep(0.4)
        r = client.get(f"/internal/develop/status/{sid2}", headers=ADMIN)
        st = r.json() if r.status_code == 200 else {}
        check(st.get("state") == "error", f"state -> error (got {st.get('state')})")
        check("502" in (st.get("error") or ""), f"error surfaces 502 (got {st.get('error')})")
        check(st.get("status") == "error", f"error tagged status=error (got {st.get('status')})")

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
