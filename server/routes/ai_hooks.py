"""Internal routes that dispatch dev-agent sessions on the sg-devbox VM.

Both endpoints require `X-Admin-Key`. They are called by:

  - The SG Discord bot (server/bot/cogs/develop_cog.py) for `/develop`.
  - The GitHub Actions self-heal workflow (.github/workflows/heal/*).

Traffic is proxied to sg-devbox's orchestrator on port 9090 via an IAP TCP
tunnel opened per-request with `gcloud compute start-iap-tunnel`. Cloud Run
already has gcloud (`google/cloud-sdk` base image on the LiteLLM service; on
the main `steamguard` service we shell out to the SDK we bundle in the image).

If IAP tunneling is unavailable (e.g. during local dev), the endpoints try a
direct HTTP call to `SG_DEVBOX_URL` first (which is only set outside prod).
"""

from __future__ import annotations

import asyncio
import hmac
import os
import shlex
import subprocess
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/internal", tags=["ai-internal"])

_ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
_DEVBOX_TOKEN = os.environ.get("DEVBOX_TOKEN", "")
_DEVBOX_URL_DIRECT = os.environ.get("SG_DEVBOX_URL", "")  # non-prod override
_ZONE = os.environ.get("SG_DEVBOX_ZONE", "us-central1-a")
_INSTANCE = os.environ.get("SG_DEVBOX_NAME", "sg-devbox")
_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "fabled-mystery-474200-i1")
_ENABLED_DEV = os.environ.get("AI_DEVELOP_ENABLED", "false").lower() == "true"
_ENABLED_HEAL = os.environ.get("AI_HEAL_ENABLED", "false").lower() == "true"
_OWNER_ID = os.environ.get("DISCORD_OWNER_ID", "1513150836472021074")
_RATE_LIMIT = int(os.environ.get("AI_DEVELOP_RATE_PER_HOUR", "5"))

# Firestore for rate limiting
try:
    from google.cloud import firestore

    _db = firestore.Client()
except Exception:  # pragma: no cover
    _db = None


class DevelopBody(BaseModel):
    discord_user_id: str
    task: str = Field(min_length=1, max_length=6000)
    thread_id: Optional[str] = None


class CreateBody(BaseModel):
    discord_user_id: str
    prompt: str = Field(min_length=1, max_length=32000)
    model: Optional[str] = None
    max_turns: int = 200
    max_thinking_tokens: int = 32000


class HealBody(BaseModel):
    run_id: str
    head_sha: str
    failure_log: str = Field(max_length=20000)


def _check_admin(header: Optional[str]) -> None:
    if not header or not _ADMIN_KEY or not hmac.compare_digest(header, _ADMIN_KEY):
        raise HTTPException(401, "bad admin key")


def _rate_check(bucket_key: str, cap: int) -> None:
    if _db is None:
        return  # no Firestore in dev — skip
    now = int(time.time())
    hour = now // 3600
    doc = _db.collection("ai_develop_rate").document(f"{bucket_key}_{hour}")
    snap = doc.get()
    n = (snap.to_dict() or {}).get("count", 0) if snap.exists else 0
    if n >= cap:
        raise HTTPException(429, f"rate limit: {cap}/hour")
    doc.set({"count": n + 1, "expires_at": (hour + 2) * 3600}, merge=True)


async def _post_session(payload: dict) -> dict:
    """Call the sg-devbox orchestrator over IAP tunnel (or direct in dev)."""
    if _DEVBOX_URL_DIRECT:
        async with httpx.AsyncClient(timeout=1500.0) as c:
            r = await c.post(
                f"{_DEVBOX_URL_DIRECT}/session",
                headers={"Authorization": f"Bearer {_DEVBOX_TOKEN}"},
                json=payload,
            )
            r.raise_for_status()
            return r.json()

    # Prod: IAP tunnel. gcloud opens a local TCP forwarder; we POST through it.
    local_port = _pick_free_port()
    tunnel = await asyncio.create_subprocess_exec(
        "gcloud",
        "compute",
        "start-iap-tunnel",
        _INSTANCE,
        "9090",
        f"--local-host-port=localhost:{local_port}",
        f"--zone={_ZONE}",
        f"--project={_PROJECT}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        # Wait for the tunnel to be ready (gcloud prints "Listening on port [N]")
        await _wait_tunnel_ready(tunnel, local_port, timeout=15)

        async with httpx.AsyncClient(timeout=1500.0) as c:
            r = await c.post(
                f"http://localhost:{local_port}/session",
                headers={"Authorization": f"Bearer {_DEVBOX_TOKEN}"},
                json=payload,
            )
            if r.status_code >= 500:
                raise HTTPException(502, f"devbox {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
            return r.json()
    finally:
        tunnel.terminate()
        try:
            await asyncio.wait_for(tunnel.wait(), timeout=5)
        except asyncio.TimeoutError:
            tunnel.kill()


def _pick_free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _wait_tunnel_ready(proc, port: int, timeout: float) -> None:
    """Poll a TCP connect to the local port until it succeeds or we time out."""
    import socket

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            await asyncio.sleep(0.4)
        if proc.returncode is not None:
            err = (await proc.stderr.read()).decode(errors="replace")[:400]
            raise HTTPException(502, f"IAP tunnel died: {err}")
    raise HTTPException(504, "IAP tunnel did not become ready in time")


@router.post("/develop")
async def develop(body: DevelopBody, x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key")):
    _check_admin(x_admin_key)
    if not _ENABLED_DEV:
        raise HTTPException(503, "develop disabled")

    if body.discord_user_id != _OWNER_ID:
        _rate_check(f"develop_{body.discord_user_id}", _RATE_LIMIT)

    return await _post_session(
        {
            "task": body.task,
            "source": "develop",
            "ref": "main",
            "initiator": body.discord_user_id,
        }
    )


_ENABLED_CREATE = os.environ.get("AI_CREATE_ENABLED", "false").lower() == "true"
_CREATE_RATE = int(os.environ.get("AI_CREATE_RATE_PER_DAY", "20"))


async def _devbox_request(method: str, path: str, json_body: Optional[dict] = None) -> dict:
    """Generic devbox HTTP call over IAP tunnel (or direct in dev). Used by
    /create + /create/status — the /develop path uses `_post_session` which
    is specialised for the synchronous /session endpoint."""
    if _DEVBOX_URL_DIRECT:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.request(
                method, f"{_DEVBOX_URL_DIRECT}{path}",
                headers={"Authorization": f"Bearer {_DEVBOX_TOKEN}"},
                json=json_body,
            )
            r.raise_for_status()
            return r.json()

    local_port = _pick_free_port()
    tunnel = await asyncio.create_subprocess_exec(
        "gcloud", "compute", "start-iap-tunnel", _INSTANCE, "9090",
        f"--local-host-port=localhost:{local_port}",
        f"--zone={_ZONE}", f"--project={_PROJECT}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        await _wait_tunnel_ready(tunnel, local_port, timeout=15)
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.request(
                method, f"http://localhost:{local_port}{path}",
                headers={"Authorization": f"Bearer {_DEVBOX_TOKEN}"},
                json=json_body,
            )
            if r.status_code >= 500:
                raise HTTPException(502, f"devbox {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
            return r.json()
    finally:
        tunnel.terminate()
        try:
            await asyncio.wait_for(tunnel.wait(), timeout=5)
        except asyncio.TimeoutError:
            tunnel.kill()


@router.post("/create")
async def create(body: CreateBody, x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key")):
    _check_admin(x_admin_key)
    if not _ENABLED_CREATE:
        raise HTTPException(503, "create disabled")

    if body.discord_user_id != _OWNER_ID:
        _rate_check(f"create_{body.discord_user_id}_day", _CREATE_RATE)

    payload = {
        "prompt": body.prompt,
        "initiator": body.discord_user_id,
        "max_turns": body.max_turns,
        "max_thinking_tokens": body.max_thinking_tokens,
    }
    if body.model:
        payload["model"] = body.model
    return await _devbox_request("POST", "/create", payload)


@router.get("/create/status/{sid}")
async def create_status(sid: str, x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key")):
    _check_admin(x_admin_key)
    if not _ENABLED_CREATE:
        raise HTTPException(503, "create disabled")
    return await _devbox_request("GET", f"/create/status/{sid}")


@router.get("/create/artifact/{sid}")
async def create_artifact(sid: str, x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key")):
    """Stream the build.zip artifact through the IAP tunnel so the Discord
    cog can attach it. Only used for artifacts ≤25MB — larger ones use signed URLs."""
    from fastapi.responses import StreamingResponse
    _check_admin(x_admin_key)
    if not _ENABLED_CREATE:
        raise HTTPException(503, "create disabled")

    # Validate sid shape
    import re as _re
    if not _re.match(r"^[a-z0-9]{6,32}$", sid):
        raise HTTPException(400, "invalid sid")

    local_port = _pick_free_port()
    tunnel = await asyncio.create_subprocess_exec(
        "gcloud", "compute", "start-iap-tunnel", _INSTANCE, "9090",
        f"--local-host-port=localhost:{local_port}",
        f"--zone={_ZONE}", f"--project={_PROJECT}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        await _wait_tunnel_ready(tunnel, local_port, timeout=15)
        client = httpx.AsyncClient(timeout=300.0)
        req = client.build_request(
            "GET", f"http://localhost:{local_port}/create/artifact/{sid}",
            headers={"Authorization": f"Bearer {_DEVBOX_TOKEN}"},
        )
        resp = await client.send(req, stream=True)

        async def _stream():
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()
                tunnel.terminate()

        return StreamingResponse(
            _stream(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="sg-create-{sid}.zip"'},
        )
    except Exception:
        tunnel.terminate()
        raise


@router.post("/heal")
async def heal(body: HealBody, x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key")):
    _check_admin(x_admin_key)
    if not _ENABLED_HEAL:
        raise HTTPException(503, "heal disabled")

    task = (
        "The main test workflow just failed on this repo. Diagnose the failure "
        "from the log below and produce the smallest patch that makes the tests "
        "pass, without changing product behavior beyond what the failing tests "
        "require. Do not touch any files on the deny-list."
    )

    return await _post_session(
        {
            "task": task,
            "source": "heal",
            "ref": body.head_sha,
            "context": body.failure_log[:15000],
            "initiator": f"gha:{body.run_id}",
        }
    )
