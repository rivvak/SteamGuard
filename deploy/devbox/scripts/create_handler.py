"""/create endpoint for the sg-devbox orchestrator.

Unlike /session (Phase 2, synchronous, worktree-based), /create is:

  * ASYNC — spawns a detached `docker run` and returns {session_id} immediately.
    The Discord cog then polls /create/status/{sid} until state is 'finished'.
  * SANDBOXED — runs in the sg-sandbox:latest container (PR #17), not on the host.
    The container is throwaway; nothing it writes to disk survives except /out.
  * SUPERVISED — the bot process may restart. We persist session state to
    /var/lib/sg-devbox/sessions/{sid}.json every heartbeat so a restart can
    reattach to a still-running `docker ps` container by name.

Endpoints registered on the parent FastAPI app:

    POST /create                          -> {session_id, container_name, state:"starting"}
    GET  /create/status/{sid}             -> {state, elapsed_s, artifact:{...}?, log_tail?}

`state` transitions:
    starting  → running  → uploading  → finished
                                     ↘ error
                                     ↘ killed
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

# ─── config ─────────────────────────────────────────────────────────────────

DEVBOX_TOKEN = os.environ["DEVBOX_TOKEN"]
SESSIONS_DIR = Path(os.environ.get("SG_SESSIONS_DIR", "/var/lib/sg-devbox/sessions"))
ARTIFACTS_DIR = Path(os.environ.get("SG_ARTIFACTS_DIR", "/var/lib/sg-devbox/artifacts"))
SANDBOX_IMAGE = os.environ.get("SG_SANDBOX_IMAGE", "sg-sandbox:latest")
# Default model for /create. Same underlying model as /develop (z-ai/glm-5.2 on
# NVIDIA NIM), but FCC requires the provider prefix in the MODEL env: the
# supported providers are 'nvidia_nim', 'zai', etc. Without the 'nvidia_nim/'
# prefix FCC rejects the config with `Invalid provider: 'z-ai'`.
# Override via SG_CREATE_MODEL env or per-request `model` param.
DEFAULT_MODEL = os.environ.get("SG_CREATE_MODEL", "nvidia_nim/z-ai/glm-5.2")
NIM_KEY = os.environ["NVIDIA_NIM_API_KEY"]
GCS_BUCKET = os.environ.get("SG_CREATE_BUCKET", "steamguard-create-artifacts")

# Discord attachment ceiling — beyond this we upload to GCS + signed URL.
DISCORD_MAX_MB = 25
GCS_MAX_MB = 500

# Docker resource caps per container
DOCKER_CPUS = os.environ.get("SG_SANDBOX_CPUS", "2")
DOCKER_MEM = os.environ.get("SG_SANDBOX_MEM", "4g")
DOCKER_PIDS = os.environ.get("SG_SANDBOX_PIDS", "512")

# Session ID sanity guard — anything the container name is built from.
_SID_RE = re.compile(r"^[a-z0-9]{6,32}$")


# ─── models ─────────────────────────────────────────────────────────────────


class CreateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=32000)
    initiator: Optional[str] = None  # discord user id
    model: Optional[str] = None  # override DEFAULT_MODEL
    max_turns: int = Field(default=200, ge=1, le=500)
    max_thinking_tokens: int = Field(default=32000, ge=0, le=64000)
    wall_clock_seconds: int = Field(default=21600, ge=60, le=21600)  # 6h cap


router = APIRouter()


# ─── helpers ────────────────────────────────────────────────────────────────


def _auth(header: Optional[str]) -> None:
    if not header or not header.startswith("Bearer "):
        raise HTTPException(401, "missing bearer")
    tok = header.split(" ", 1)[1].strip()
    if not _consteq(tok, DEVBOX_TOKEN):
        raise HTTPException(401, "bad bearer")


def _consteq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    r = 0
    for x, y in zip(a.encode(), b.encode()):
        r |= x ^ y
    return r == 0


def _sid_path(sid: str) -> Path:
    if not _SID_RE.match(sid):
        raise HTTPException(400, "invalid session id")
    return SESSIONS_DIR / f"{sid}.json"


def _load(sid: str) -> dict:
    p = _sid_path(sid)
    if not p.exists():
        raise HTTPException(404, "unknown session")
    return json.loads(p.read_text())


def _save(sid: str, state: dict) -> None:
    _sid_path(sid).write_text(json.dumps(state))


def _now() -> int:
    return int(time.time())


_gh_token_cache: dict = {"token": None, "exp": 0}


def _fetch_gh_token() -> str:
    """Mint (or reuse a cached) GitHub App installation token for the sandbox.

    Sandboxes cannot push (they're throwaway), but `gh api` / `gh search repos`
    still need a token for research. We cache tokens for 50 minutes
    (GH installation tokens live 60m)."""
    now = _now()
    if _gh_token_cache["token"] and _gh_token_cache["exp"] > now + 60:
        return _gh_token_cache["token"]

    app_id = os.environ.get("SG_HEAL_APP_ID", "")
    install_id = os.environ.get("SG_HEAL_INSTALLATION_ID", "")
    pem_b64 = os.environ.get("SG_HEAL_PRIVATE_KEY_B64", "")
    if not (app_id and install_id and pem_b64):
        # Env-provided token path (rare — for dev/manual runs)
        return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""

    import base64
    import jwt as _jwt
    import httpx as _httpx

    pem = base64.b64decode(pem_b64).decode()
    jwt_tok = _jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": app_id},
        pem, algorithm="RS256",
    )
    r = _httpx.post(
        f"https://api.github.com/app/installations/{install_id}/access_tokens",
        headers={"Authorization": f"Bearer {jwt_tok}", "Accept": "application/vnd.github+json"},
        timeout=15.0,
    )
    r.raise_for_status()
    data = r.json()
    _gh_token_cache["token"] = data["token"]
    # Cache slightly less than 1h (GH tokens expire in 60m)
    _gh_token_cache["exp"] = now + 3000
    return _gh_token_cache["token"]


def _container_name(sid: str) -> str:
    return f"sg-create-{sid}"


def _docker_ps_running(name: str) -> bool:
    r = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return name in r.stdout.split()


def _docker_inspect_exit_code(name: str) -> Optional[int]:
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.ExitCode}}", name],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


# ─── routes ─────────────────────────────────────────────────────────────────


@router.post("/create")
def create(body: CreateBody, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)

    sid = uuid.uuid4().hex[:12]
    name = _container_name(sid)
    out_dir = ARTIFACTS_DIR / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o755)

    model = body.model or DEFAULT_MODEL
    gh_token = _fetch_gh_token()

    # Spawn detached — do NOT block the request. The sandbox writes /out/status.json
    # and /out/build.zip; we poll from /create/status.
    # Ensure /out is writable by uid 1000 (the `builder` user inside the container).
    # The orchestrator runs as `sgagent`, so out_dir would default to sgagent-owned.
    # We chmod 0777 for the bind mount to avoid uid-mismatch permission errors
    # (the dir is per-session throwaway inside /var/lib/sg-devbox/artifacts/<sid>,
    # so this is safe).
    try:
        os.chmod(out_dir, 0o777)
    except OSError:
        pass

    cmd = [
        "docker", "run", "--rm", "--detach",
        "--name", name,
        "--cpus", DOCKER_CPUS,
        "--memory", DOCKER_MEM,
        "--pids-limit", DOCKER_PIDS,
        "--read-only",  # sandbox writes only to explicit tmpfs + /out
        # tmpfs mounts — CRITICAL: uid=1000/gid=1000 so `builder` user (UID 1000)
        # can write to them. Without these opts, tmpfs mounts default to root:root
        # and git config / claude / FCC all crash with EACCES in ~1s.
        "--tmpfs", "/tmp:rw,size=1g,uid=1000,gid=1000,mode=1777",
        "--tmpfs", "/workspace:rw,size=8g,uid=1000,gid=1000,mode=0755",
        "--tmpfs", "/home/builder:rw,size=512m,uid=1000,gid=1000,mode=0755",
        "-v", f"{out_dir}:/out:rw",
        "-e", f"NVIDIA_NIM_API_KEY={NIM_KEY}",
        "-e", f"GITHUB_TOKEN={gh_token}",
        "-e", f"MODEL={model}",
        "-e", f"SESSION_ID={sid}",
        "-e", f"MAX_TURNS={body.max_turns}",
        "-e", f"MAX_THINKING_TOKENS={body.max_thinking_tokens}",
        "-e", f"WALL_CLOCK_SECONDS={body.wall_clock_seconds}",
        # PROMPT via env is fine up to ~128KB on Linux; the schema caps at 32k.
        "-e", f"PROMPT={body.prompt}",
        SANDBOX_IMAGE,
    ]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        # Persist the failure so /create/status/{sid} can report it
        err_state = {
            "session_id": sid,
            "container_name": name,
            "state": "error",
            "started_at": _now(),
            "ended_at": _now(),
            "model": model,
            "initiator": body.initiator,
            "prompt_len": len(body.prompt),
            "out_dir": str(out_dir),
            "error": f"docker run failed rc={r.returncode}: {(r.stdout + r.stderr)[-800:]}",
        }
        _save(sid, err_state)
        return {
            "status": "error",
            "session_id": sid,
            "error": err_state["error"],
            "log_tail": (r.stdout + r.stderr)[-1500:],
        }
    container_id = r.stdout.strip()

    state = {
        "session_id": sid,
        "container_name": name,
        "container_id": container_id,
        "state": "starting",
        "started_at": _now(),
        "model": model,
        "initiator": body.initiator,
        "prompt_len": len(body.prompt),
        "out_dir": str(out_dir),
    }
    _save(sid, state)

    return {
        "status": "queued",
        "session_id": sid,
        "container_name": name,
        "state": "starting",
    }


@router.get("/create/artifact/{sid}")
def create_artifact(sid: str, authorization: Optional[str] = Header(default=None)):
    """Return the build.zip artifact bytes. Used by the license-server route
    to stream to Discord when the artifact fits under Discord's 25MB cap."""
    _auth(authorization)
    st = _load(sid)
    zip_path = Path(st["out_dir"]) / "build.zip"
    if not zip_path.exists():
        raise HTTPException(404, "artifact not ready")
    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=f"sg-create-{sid}.zip",
    )


@router.get("/create/status/{sid}")
def create_status(sid: str, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    st = _load(sid)
    name = st["container_name"]
    out_dir = Path(st["out_dir"])

    # If we've already reached a terminal state and produced an artifact URL,
    # short-circuit — no need to re-check docker.
    if st["state"] in ("finished", "error", "killed"):
        return _augment(st)

    # Look at sandbox-reported status first (authoritative when present).
    sandbox_status = _read_sandbox_status(out_dir)
    running = _docker_ps_running(name)

    if running:
        if sandbox_status and sandbox_status.get("state") == "running":
            st["state"] = "running"
        elif st["state"] == "starting":
            st["state"] = "running"
    else:
        # Container is gone → either finished or crashed. Decide by exit code +
        # artifact presence.
        exit_code = _docker_inspect_exit_code(name)
        if exit_code is None and sandbox_status:
            exit_code = sandbox_status.get("exit_code")

        zip_path = out_dir / "build.zip"
        if zip_path.exists():
            # Upload if larger than Discord's ceiling.
            st["exit_code"] = exit_code
            st["state"] = "uploading"
            _save(sid, st)
            _maybe_upload(sid, st, out_dir)  # mutates st
            st["state"] = "finished" if exit_code == 0 else "killed"
        else:
            st["state"] = "error"
            st["error"] = f"container exited with code {exit_code} and no artifact"

        st["ended_at"] = _now()

    _save(sid, st)
    return _augment(st)


# ─── internals ──────────────────────────────────────────────────────────────


def _read_sandbox_status(out_dir: Path) -> Optional[dict]:
    p = out_dir / "status.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _augment(st: dict) -> dict:
    resp = dict(st)
    resp["elapsed_s"] = _now() - st["started_at"]
    log = Path(st["out_dir"]) / "claude.log"
    if log.exists():
        resp["log_tail"] = log.read_text(errors="replace")[-1500:]
    return resp


def _maybe_upload(sid: str, st: dict, out_dir: Path) -> None:
    """If artifact > DISCORD_MAX_MB, upload to GCS and mint a 7-day signed URL.
    Otherwise leave the local path for the caller to attach directly."""
    zip_path = out_dir / "build.zip"
    size = zip_path.stat().st_size
    st["artifact_size_bytes"] = size
    st["artifact_size_human"] = _human_bytes(size)
    size_mb = size / (1024 * 1024)

    if size_mb <= DISCORD_MAX_MB:
        st["artifact"] = {"kind": "attachment", "path": str(zip_path), "size_bytes": size}
        return

    if size_mb > GCS_MAX_MB:
        st["artifact"] = {"kind": "too_large", "size_bytes": size}
        st["error"] = f"artifact {_human_bytes(size)} exceeds {GCS_MAX_MB}MB cap"
        return

    # Upload → gcs://<bucket>/<yyyy>/<mm>/<sid>.zip
    ts = time.strftime("%Y/%m")
    gcs_path = f"gs://{GCS_BUCKET}/{ts}/{sid}.zip"
    up = subprocess.run(
        ["gcloud", "storage", "cp", str(zip_path), gcs_path, "--quiet"],
        capture_output=True, text=True, timeout=600,
    )
    if up.returncode != 0:
        st["artifact"] = {"kind": "error"}
        st["error"] = f"gcs upload failed: {up.stderr[-500:]}"
        return

    # Signed URL, 7-day TTL. Uses the VM's default SA (already granted
    # iam.serviceAccountTokenCreator on itself during prep).
    su = subprocess.run(
        ["gcloud", "storage", "sign-url", gcs_path,
         "--duration=7d",
         "--format=value(signed_url)",
         "--impersonate-service-account",
         os.environ.get("SG_DEVBOX_SA", ""),
         ],
        capture_output=True, text=True, timeout=60,
    )
    # If SG_DEVBOX_SA env is empty, gcloud will fall back to the VM's own SA
    # via metadata server (which is what we want anyway).
    if su.returncode != 0:
        # Retry without impersonation (default credentials on VM = SA already).
        su = subprocess.run(
            ["gcloud", "storage", "sign-url", gcs_path,
             "--duration=7d",
             "--format=value(signed_url)"],
            capture_output=True, text=True, timeout=60,
        )
    if su.returncode != 0:
        st["artifact"] = {"kind": "gcs_no_signed_url", "gcs_path": gcs_path}
        st["error"] = f"sign-url failed: {su.stderr[-500:]}"
        return

    signed_url = su.stdout.strip().splitlines()[-1] if su.stdout.strip() else ""
    st["artifact"] = {
        "kind": "signed_url",
        "gcs_path": gcs_path,
        "url": signed_url,
        "size_bytes": size,
        "expires_in_days": 7,
    }
