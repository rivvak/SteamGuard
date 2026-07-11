"""sg-devbox orchestrator — receives task requests from the SG server (via IAP
tunnel), spawns Claude Code in a fresh worktree, and classifies the result.

Runs as `sgagent`, bound to 127.0.0.1:9090. Never publicly reachable.

Endpoints:

    GET  /health                     -> {"ok": true, "fcc": "ok"|"down"}
    POST /session                    -> Body: {task, source, ref?}
                                        Header: Authorization: Bearer $DEVBOX_TOKEN
                                        Returns: {status, ...}

`status` is one of:

    committed      — All changes were in the allow-list; pushed to `main`.
                     Extra: commit_sha, files, commit_url.
    pr_opened      — Some changes outside allow-list, none denied. PR opened.
                     Extra: pr_number, pr_url, files.
    refused        — At least one file matched DENY. Nothing pushed.
                     Extra: denied_files.
    empty          — Claude Code made no changes.
    error          — Something blew up. Extra: error, log_tail.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

# Import path-guard rules from the repo (baked into the checkout)
REPO = Path("/home/sgagent/SteamGuard")
sys.path.insert(0, str(REPO))
from ai.guards.diff_check import classify_diff  # noqa: E402

DEVBOX_TOKEN = os.environ["DEVBOX_TOKEN"]
FCC_URL = os.environ.get("FCC_URL", "http://127.0.0.1:8082")
NIM_KEY = os.environ.get("NVIDIA_NIM_API_KEY", "")
MODEL = os.environ.get("AI_DEVBOX_MODEL", "nvidia_nim/z-ai/glm-5.2")
MODELS_RAW = os.environ.get(
    "AI_DEVBOX_MODELS",
    "nvidia_nim/z-ai/glm-5.2,nvidia_nim/deepseek-ai/deepseek-v4-pro,nvidia_nim/deepseek-ai/deepseek-v4-flash",
)
MAX_RUNTIME_SECONDS = int(os.environ.get("AI_DEVBOX_MAX_RUNTIME_SECONDS", "2400"))
PLAN_RUNTIME_SECONDS = int(os.environ.get("AI_DEVBOX_PLAN_RUNTIME_SECONDS", "900"))
MAX_TURNS = int(os.environ.get("AI_DEVBOX_MAX_TURNS", "40"))
PLAN_MAX_TURNS = int(os.environ.get("AI_DEVBOX_PLAN_MAX_TURNS", "18"))
ATTEMPT_TIMEOUT_SECONDS = int(os.environ.get("AI_DEVBOX_ATTEMPT_TIMEOUT_SECONDS", "900"))
PLAN_ATTEMPT_TIMEOUT_SECONDS = int(
    os.environ.get("AI_DEVBOX_PLAN_ATTEMPT_TIMEOUT_SECONDS", "180")
)
MAX_ATTEMPTS = int(os.environ.get("AI_DEVBOX_MAX_ATTEMPTS", "4"))
PLAN_MAX_ATTEMPTS = int(os.environ.get("AI_DEVBOX_PLAN_MAX_ATTEMPTS", "2"))
PROGRESS_UPDATE_SECONDS = max(5, int(os.environ.get("AI_DEVBOX_PROGRESS_UPDATE_SECONDS", "15")))
STATUS_LOG_TAIL_CHARS = max(400, int(os.environ.get("AI_DEVBOX_STATUS_LOG_TAIL_CHARS", "1200")))
WORK_ROOT = Path(os.environ.get("SG_WORK_ROOT", "/var/lib/sg-devbox/work"))
REPO_SLUG = os.environ.get("REPO_SLUG", "rivvak/SteamGuard")
OPEN_PR = Path("/opt/sg-devbox/open-pr.sh")
DEVELOP_SESSIONS_DIR = Path(
    os.environ.get("SG_DEVELOP_SESSIONS_DIR", "/var/lib/sg-devbox/develop-sessions")
)
_SID_RE = re.compile(r"^[a-z0-9]{6,32}$")

app = FastAPI(title="sg-devbox orchestrator")

# Register /create + /create/status/{sid} routes (Phase 3, async, containerised).
# Kept in a separate module so this file stays focused on the /session flow.
from create_handler import router as _create_router  # noqa: E402

app.include_router(_create_router)

# Register /memory/* routes (Phase 4).
from memory_routes import router as _memory_router  # noqa: E402
import memory_store  # noqa: E402

app.include_router(_memory_router)


class SessionBody(BaseModel):
    task: str = Field(min_length=1, max_length=8000)
    source: str = Field(default="develop", pattern="^(develop|heal)$")
    ref: str = Field(default="main")
    context: Optional[str] = None  # e.g. failure log for heal
    initiator: Optional[str] = None  # discord user id or GH run id
    channel_id: Optional[str] = None  # Phase 4: discord channel id (memory scope)
    deep: bool = False                # Phase 4: deep-reasoning mode toggle
    plan_only: bool = False
    conversation: Optional[list[dict]] = None
    active_objective: Optional[str] = None


class FollowupBody(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    plan_only: bool = False


_develop_locks_guard = threading.Lock()
_develop_locks: dict[str, threading.Lock] = {}


def _split_csv(raw: str) -> list[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def _candidate_models() -> list[str]:
    vals = _split_csv(MODELS_RAW)
    if not vals:
        vals = [MODEL]
    if MODEL and MODEL not in vals:
        vals.insert(0, MODEL)
    return vals


def _candidate_auth_tokens() -> list[str]:
    vals = [
        os.environ.get("AI_DEVBOX_AUTH_TOKEN_A", ""),
        os.environ.get("AI_DEVBOX_AUTH_TOKEN_B", ""),
        os.environ.get("AI_DEVBOX_AUTH_TOKEN_C", ""),
        DEVBOX_TOKEN,
    ]
    out: list[str] = []
    for v in vals:
        if v and v not in out:
            out.append(v)
    return out


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


def _run(
    cmd: list[str],
    cwd: Path,
    env: Optional[dict] = None,
    timeout: int = 900,
    stdin_data: Optional[str] = None,
) -> subprocess.CompletedProcess:
    # When env is provided, use it AS-IS (FCC launcher strips ANTHROPIC_* from parent).
    # When env is None, inherit process env.
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env if env is not None else None,
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@app.get("/health")
def health():
    try:
        r = httpx.get(f"{FCC_URL}/health", timeout=3.0)
        fcc = "ok" if r.status_code < 500 else f"degraded:{r.status_code}"
    except Exception as e:
        fcc = f"down:{type(e).__name__}"
    return {"ok": True, "fcc": fcc, "model": MODEL, "worktree_root": str(WORK_ROOT)}


@app.post("/session")
def session(body: SessionBody, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    sid = uuid.uuid4().hex[:12]
    return _execute_session(body, sid)


@app.post("/develop")
def develop(body: SessionBody, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    sid = uuid.uuid4().hex[:12]
    state = {
        "session_id": sid,
        "state": "starting",
        "started_at": int(time.time()),
        "source": body.source,
        "initiator": body.initiator,
        "ref": body.ref,
        "deep": body.deep,
        "channel_id": body.channel_id,
        "task": body.task,
        "active_objective": body.active_objective or body.task,
        "progress": {
            "stage": "queued",
            "detail": "Session queued; worker starting.",
            "updated_at": int(time.time()),
        },
        "messages": [
            {"role": "user", "content": body.task, "ts": int(time.time()), "kind": "objective"}
        ],
    }
    _develop_save(sid, state)
    t = threading.Thread(
        target=_develop_worker,
        args=(sid, body.dict()),
        daemon=True,
        name=f"develop-{sid}",
    )
    t.start()
    return {"status": "queued", "session_id": sid, "state": "starting"}


@app.get("/develop/status/{sid}")
def develop_status(sid: str, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    st = _develop_load(sid)
    st["elapsed_s"] = max(0, int(time.time()) - int(st.get("started_at", int(time.time()))))
    return st


@app.post("/develop/{sid}/message")
def develop_message(sid: str, body: FollowupBody, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    st = _develop_load(sid)
    if st.get("state") in ("starting", "running"):
        raise HTTPException(409, "session is currently running")
    messages = st.get("messages") or []
    messages.append({"role": "user", "content": body.message, "ts": int(time.time()), "kind": "follow_up"})
    st["messages"] = messages[-20:]
    st["active_objective"] = body.message
    st["state"] = "starting"
    st["started_at"] = int(time.time())
    st.pop("ended_at", None)
    st.pop("error", None)
    st.pop("log_tail", None)
    st.pop("result", None)
    st["attempts"] = []
    st["progress"] = {
        "stage": "queued",
        "detail": "Follow-up accepted; worker starting.",
        "updated_at": int(time.time()),
    }
    _develop_save(sid, st)

    followup_context = (
        "## Follow-up request\n\n"
        f"{body.message}\n\n"
        "Resolve this against the objective and conversation context above.\n"
    )
    worker_body = {
        "task": st.get("task") or body.message,
        "source": st.get("source", "develop"),
        "ref": st.get("ref", "main"),
        "context": followup_context,
        "initiator": st.get("initiator"),
        "channel_id": st.get("channel_id"),
        "deep": bool(st.get("deep", False)),
        "plan_only": body.plan_only,
        "conversation": st.get("messages") or [],
        "active_objective": st.get("active_objective") or st.get("task") or body.message,
    }
    t = threading.Thread(
        target=_develop_worker,
        args=(sid, worker_body),
        daemon=True,
        name=f"develop-followup-{sid}",
    )
    t.start()
    return {"status": "queued", "session_id": sid, "state": "starting"}


@app.get("/develop/{sid}/history")
def develop_history(sid: str, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    st = _develop_load(sid)
    return {
        "session_id": sid,
        "state": st.get("state"),
        "active_objective": st.get("active_objective"),
        "progress": st.get("progress"),
        "attempts": st.get("attempts", []),
        "messages": st.get("messages", []),
        "last_result": st.get("result"),
    }


def _develop_save(sid: str, state: dict) -> None:
    DEVELOP_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if not _SID_RE.match(sid):
        raise HTTPException(400, "invalid session id")
    state["updated_at"] = int(time.time())
    target = DEVELOP_SESSIONS_DIR / f"{sid}.json"
    temp = DEVELOP_SESSIONS_DIR / f"{sid}.json.tmp"
    temp.write_text(json.dumps(state), encoding="utf-8")
    temp.replace(target)


def _develop_load(sid: str) -> dict:
    if not _SID_RE.match(sid):
        raise HTTPException(400, "invalid session id")
    p = DEVELOP_SESSIONS_DIR / f"{sid}.json"
    if not p.exists():
        raise HTTPException(404, "unknown session")
    return json.loads(p.read_text(encoding="utf-8"))


def _develop_worker(sid: str, body_data: dict) -> None:
    lock = _get_develop_lock(sid)
    with lock:
        _develop_worker_locked(sid, body_data)


def _develop_worker_locked(sid: str, body_data: dict) -> None:
    def progress_cb(update: dict) -> None:
        st = _develop_load(sid)
        progress = st.get("progress") or {}
        progress.update(update.get("progress") or {})
        if progress:
            progress["updated_at"] = int(time.time())
            st["progress"] = progress
        if "log_tail" in update:
            st["log_tail"] = str(update["log_tail"] or "")[-STATUS_LOG_TAIL_CHARS:]
        if "attempts" in update:
            st["attempts"] = list(update["attempts"] or [])[-8:]
        if "error" in update:
            st["error"] = update["error"]
        _develop_save(sid, st)

    try:
        st = _develop_load(sid)
        st["state"] = "running"
        st["attempts"] = []
        st.pop("error", None)
        st["progress"] = {
            "stage": "preparing",
            "detail": "Preparing worktree and prompt.",
            "updated_at": int(time.time()),
        }
        _develop_save(sid, st)
        result = _execute_session(SessionBody(**body_data), sid, progress_cb=progress_cb)
        st["result"] = result
        st["state"] = "error" if result.get("status") == "error" else "finished"
        st["ended_at"] = int(time.time())
        if st["state"] != "error":
            st.pop("error", None)
        st["attempts"] = result.get("attempts", st.get("attempts", []))
        st["log_tail"] = str(result.get("log_tail") or st.get("log_tail") or "")[-STATUS_LOG_TAIL_CHARS:]
        st["progress"] = {
            "stage": "finished" if st["state"] == "finished" else "error",
            "detail": _result_summary(result)[:240],
            "updated_at": int(time.time()),
        }
        msgs = st.get("messages") or []
        msgs.append(
            {
                "role": "assistant",
                "content": _result_summary(result),
                "ts": int(time.time()),
                "kind": "result",
            }
        )
        st["messages"] = msgs[-20:]
        _develop_save(sid, st)
    except Exception as e:
        try:
            st = _develop_load(sid)
        except Exception:
            st = {"session_id": sid, "started_at": int(time.time())}
        st["state"] = "error"
        st["ended_at"] = int(time.time())
        st["progress"] = {
            "stage": "error",
            "detail": f"Worker exception: {type(e).__name__}: {e}",
            "updated_at": int(time.time()),
        }
        st["result"] = {
            "status": "error",
            "session_id": sid,
            "error": f"develop worker exception: {type(e).__name__}: {e}",
        }
        msgs = st.get("messages") or []
        msgs.append(
            {
                "role": "assistant",
                "content": f"Error: {type(e).__name__}: {e}",
                "ts": int(time.time()),
                "kind": "result",
            }
        )
        st["messages"] = msgs[-20:]
        _develop_save(sid, st)


def _get_develop_lock(sid: str) -> threading.Lock:
    with _develop_locks_guard:
        lk = _develop_locks.get(sid)
        if lk is None:
            lk = threading.Lock()
            _develop_locks[sid] = lk
        return lk


def _wait_fcc_ready(timeout_s: int = 45, auth_token: Optional[str] = None) -> Optional[str]:
    end = time.monotonic() + timeout_s
    token = auth_token or DEVBOX_TOKEN
    auth = {"Authorization": f"Bearer {token}"}
    last_err = "unknown"
    while time.monotonic() < end:
        try:
            r = httpx.get(f"{FCC_URL}/v1/models", headers=auth, timeout=3.0)
            if r.status_code == 200:
                return None
            last_err = f"fcc {r.status_code}: {r.text[:180]}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(0.8)
    return last_err


def _execute_session(
    body: SessionBody,
    sid: str,
    progress_cb: Optional[Callable[[dict], None]] = None,
) -> dict:
    wt = WORK_ROOT / sid
    WORK_ROOT.mkdir(parents=True, exist_ok=True)

    # 1) fresh worktree from origin/<ref>
    _progress(
        progress_cb,
        stage="preparing_worktree",
        detail=f"Fetching origin/{body.ref}.",
    )
    up = _run(["git", "fetch", "origin", body.ref], REPO)
    if up.returncode != 0:
        return _error(sid, "git fetch failed", up.stderr)
    _progress(
        progress_cb,
        stage="preparing_worktree",
        detail=f"Creating fresh worktree from origin/{body.ref}.",
    )
    add = _run(["git", "worktree", "add", "-f", str(wt), f"origin/{body.ref}"], REPO)
    if add.returncode != 0:
        return _error(sid, "git worktree add failed", add.stderr)

    # 2) build the prompt (Claude Code reads from stdin in -p mode)
    # Phase 4: prepend PRIOR_CONTEXT (per-user + per-channel memory) if any.
    prior_ctx = ""
    try:
        prior_ctx = memory_store.build_preamble(
            user_id=body.initiator or "",
            channel_id=body.channel_id or "",
        )
    except Exception:
        prior_ctx = ""

    reasoning_hint = ""
    if body.deep:
        reasoning_hint = (
            "## Deep-reasoning mode ENABLED\n"
            "Take extra time. Think through edge cases before editing files.\n"
            "Use WebSearch/WebFetch when APIs, versions, or docs may be relevant.\n"
            "Prefer correctness over speed.\n\n"
        )

    prompt = (
        f"# SteamGuard AI task ({body.source})\n\n"
        f"Session id: {sid}\n"
        f"Initiator: {body.initiator or 'unknown'}\n\n"
        + (f"## Active objective\n\n{body.active_objective}\n\n" if body.active_objective else "")
        + (_conversation_block(body.conversation) if body.conversation else "")
        + (f"## Prior context (Phase 4 memory)\n\n{prior_ctx}\n" if prior_ctx else "")
        + reasoning_hint
        + "## Rules\n"
        + "- Never touch: auth/, license/entitlement/hwid/cert/pinning code, "
        + ".env*, *.pem/*.key, .github/workflows/**, Dockerfiles, build scripts, hashes.txt.\n"
        + "- Prefer minimal, testable changes. Edit files in the current working directory.\n"
        + (
            "- PLAN-ONLY MODE: do NOT edit files; return a concrete implementation plan with steps and risks.\n"
            if body.plan_only
            else ""
        )
        + "- After you finish, exit — do not commit; the orchestrator handles git.\n\n"
        + "## Task\n\n"
        + body.task
        + (f"\n\n## Context\n\n{body.context}\n" if body.context else "")
    )
    # Also drop a copy on disk for debugging.
    (wt / ".sg-task.md").write_text(prompt)
    _progress(
        progress_cb,
        stage="starting_agent",
        detail="Prompt prepared; starting Claude Code.",
    )

    # 3) invoke Claude Code with model/token failover for reliability.
    cc, log_tail, attempt_meta = _run_claude_with_failover(wt, body, prompt, progress_cb=progress_cb)
    if cc is None:
        _cleanup(wt)
        return _error(
            sid,
            "claude run failed on all model/token attempts",
            log_tail,
            attempts=attempt_meta,
        )

    # 4) classify diff
    _progress(
        progress_cb,
        stage="classifying_diff",
        detail="Claude Code finished; inspecting repository changes.",
        log_tail=log_tail,
        attempts=attempt_meta,
    )
    diff = _run(["git", "diff", "--name-only"], wt)
    files = [ln for ln in diff.stdout.splitlines() if ln.strip()]
    if not files:
        _cleanup(wt)
        if body.plan_only:
            return {
                "status": "planned",
                "session_id": sid,
                "plan": _extract_plan(cc.stdout),
                "log_tail": log_tail,
                "attempts": attempt_meta,
            }
        return {
            "status": "empty",
            "session_id": sid,
            "log_tail": log_tail,
            "attempts": attempt_meta,
            "next_step_hint": (
                "No diff produced. For big tasks, run plan_only first, then continue_last with a specific step."
            ),
        }

    if body.plan_only:
        _cleanup(wt)
        return {
            "status": "planned",
            "session_id": sid,
            "plan": _extract_plan(cc.stdout),
            "warning": "Plan mode produced file edits; changes were discarded.",
            "proposed_files": files[:30],
            "log_tail": log_tail,
            "attempts": attempt_meta,
        }

    cls = classify_diff(files)

    if cls.classification == "denied":
        _cleanup(wt)
        _record_memory(body, sid, "refused", None, log_tail)
        return {
            "status": "refused",
            "session_id": sid,
            "denied_files": cls.denied_files,
            "log_tail": log_tail,
            "attempts": attempt_meta,
        }

    # 5) stage + commit
    _progress(
        progress_cb,
        stage="committing",
        detail=f"Preparing {cls.classification} git commit.",
        attempts=attempt_meta,
        log_tail=log_tail,
    )
    _run(["git", "add", "-A"], wt)
    commit_msg = _build_commit_msg(body, sid, cls.classification)
    # No -S: signing is disabled per operator preference (commit.gpgsign=false).
    # If signing is later re-enabled, add a signing key + set commit.gpgsign=true
    # in the sgagent git config and this commit will pick it up automatically.
    ci = _run(["git", "commit", "-m", commit_msg], wt)
    if ci.returncode != 0:
        _cleanup(wt)
        return _error(sid, "git commit failed", ci.stderr)

    if cls.classification == "direct":
        _progress(
            progress_cb,
            stage="pushing",
            detail=f"Pushing changes directly to {body.ref}.",
            attempts=attempt_meta,
        )
        push = _run(["git", "push", "origin", f"HEAD:{body.ref}"], wt)
        if push.returncode != 0:
            _cleanup(wt)
            return _error(sid, "git push (direct) failed", push.stderr)
        sha = _run(["git", "rev-parse", "HEAD"], wt).stdout.strip()
        _cleanup(wt)
        commit_url = f"https://github.com/{REPO_SLUG}/commit/{sha}"
        _record_memory(body, sid, "committed", commit_url, log_tail)
        return {
            "status": "committed",
            "session_id": sid,
            "commit_sha": sha,
            "commit_url": commit_url,
            "files": files,
            "attempts": attempt_meta,
        }

    # else pr
    branch = f"sg-heal/{body.source}-{sid}"
    _run(["git", "branch", "-M", branch], wt)
    _progress(
        progress_cb,
        stage="opening_pr",
        detail=f"Pushing branch {branch} and opening PR.",
        attempts=attempt_meta,
    )
    pr = _run(
        ["/opt/sg-devbox/open-pr.sh", branch, body.task[:72].replace("\n", " "), body.source],
        wt,
        timeout=180,
    )
    if pr.returncode != 0:
        _cleanup(wt)
        return _error(sid, "open-pr.sh failed", pr.stdout + pr.stderr)
    pr_url = pr.stdout.strip().splitlines()[-1] if pr.stdout.strip() else ""
    _cleanup(wt)
    _record_memory(body, sid, "pr_opened", pr_url, log_tail)
    return {
        "status": "pr_opened",
        "session_id": sid,
        "pr_url": pr_url,
        "files": files,
        "attempts": attempt_meta,
    }


def _build_commit_msg(body: SessionBody, sid: str, classification: str) -> str:
    scope = "docs" if classification == "direct" else "ai"
    title = body.task.strip().splitlines()[0][:64]
    return (
        f"{scope}(ai): {title}\n\n"
        f"Automated change by rivvak-sg-heal[bot].\n"
        f"Source: {body.source}\n"
        f"Session: {sid}\n"
        f"Initiator: {body.initiator or 'unknown'}\n"
        f"Path-guard: {classification}\n"
    )


def _cleanup(wt: Path) -> None:
    try:
        _run(["git", "worktree", "remove", "--force", str(wt)], REPO, timeout=30)
    except Exception:
        pass


def _error(sid: str, msg: str, tail: str, attempts: Optional[list[dict]] = None):
    out = {"status": "error", "session_id": sid, "error": msg, "log_tail": tail[-1500:]}
    if attempts:
        out["attempts"] = attempts
    return out


def _record_memory(
    body: SessionBody,
    sid: str,
    status: str,
    artifact_url: Optional[str],
    log_tail: str,
) -> None:
    """Best-effort append this /develop session into per-user + per-channel memory."""
    try:
        memory_store.record_completion(
            command=body.source,
            session_id=sid,
            prompt=body.task,
            initiator=body.initiator or "",
            channel_id=body.channel_id or "",
            status=status,
            artifact_url=artifact_url,
            log_tail=log_tail,
        )
    except Exception:
        pass


def _extract_plan(stdout_text: str) -> str:
    text = (stdout_text or "").strip()
    if not text:
        return "No plan text was produced."
    return text[-3000:]


def _conversation_block(conversation: list[dict]) -> str:
    lines = ["## Conversation context (latest first)\n"]
    for m in conversation[-12:]:
        role = (m.get("role") or "unknown").upper()
        content = str(m.get("content") or "")[:1200]
        lines.append(f"{role}: {content}")
    return "\n".join(lines) + "\n\n"


def _result_summary(result: dict) -> str:
    status = result.get("status", "unknown")
    if status == "planned":
        plan = str(result.get("plan") or "")
        return f"Planned response ready.\n{plan[:600]}"
    if status == "error":
        return f"Error: {result.get('error', 'unknown')}"
    if status == "empty":
        return "Run completed with no repository changes."
    if status == "pr_opened":
        return f"Opened PR: {result.get('pr_url', '(missing url)')}"
    if status == "committed":
        return f"Committed: {result.get('commit_url', result.get('commit_sha', '(missing commit)'))}"
    if status == "refused":
        return f"Refused due to denied files: {', '.join(result.get('denied_files', [])[:8])}"
    return f"Run completed with status: {status}"


def _run_claude_with_failover(
    wt: Path,
    body: SessionBody,
    prompt: str,
    progress_cb: Optional[Callable[[dict], None]] = None,
) -> tuple[Optional[subprocess.CompletedProcess], str, list[dict]]:
    models = _candidate_models()
    tokens = _candidate_auth_tokens()
    attempts: list[dict] = []
    last_tail = ""
    total_budget_seconds = PLAN_RUNTIME_SECONDS if body.plan_only else MAX_RUNTIME_SECONDS
    per_attempt_timeout = (
        PLAN_ATTEMPT_TIMEOUT_SECONDS if body.plan_only else ATTEMPT_TIMEOUT_SECONDS
    )
    per_attempt_turns = PLAN_MAX_TURNS if body.plan_only else MAX_TURNS
    max_attempts = PLAN_MAX_ATTEMPTS if body.plan_only else MAX_ATTEMPTS
    total_budget_seconds = max(30, total_budget_seconds)
    per_attempt_timeout = max(30, min(per_attempt_timeout, total_budget_seconds))
    max_attempts = max(1, max_attempts)
    started = time.monotonic()
    claude_attempts = 0
    auth_index = _first_ready_auth_token(
        tokens,
        attempts,
        progress_cb,
        detail="Checking Claude gateway auth token.",
        started=started,
        total_budget_seconds=total_budget_seconds,
    )
    if auth_index is None:
        return None, "Claude gateway did not accept any configured auth token.", attempts

    model_index = 0
    while claude_attempts < max_attempts and model_index < len(models):
        elapsed = int(time.monotonic() - started)
        remaining = max(0, total_budget_seconds - elapsed)
        if remaining < 30:
            last_tail = (
                f"total runtime budget exhausted after {elapsed}s "
                f"(budget {total_budget_seconds}s)"
            )
            attempts.append(
                {
                    "error": "budget_exhausted",
                    "elapsed_s": elapsed,
                    "budget_s": total_budget_seconds,
                }
            )
            break

        model_name = models[model_index]
        auth_token = tokens[auth_index]
        timeout_s = min(per_attempt_timeout, remaining)
        claude_attempts += 1
        attempt_no = claude_attempts
        _progress(
            progress_cb,
            stage="running_agent",
            detail=(
                f"Claude attempt {attempt_no}/{max_attempts} on {model_name} "
                f"(timeout {timeout_s}s, budget left {remaining}s)."
            ),
            attempts=attempts,
            progress={
                "attempt": attempt_no,
                "attempt_limit": max_attempts,
                "model": model_name,
                "auth_slot": _mask_token(auth_token),
                "timeout_s": timeout_s,
                "budget_remaining_s": remaining,
            },
        )

        base_env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
        env = {
            **base_env,
            "ANTHROPIC_BASE_URL": FCC_URL,
            "MODEL": model_name,
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "190000",
            "ANTHROPIC_AUTH_TOKEN": auth_token,
        }
        try:
            cc = _run_claude_attempt(
                [
                    "claude",
                    "-p",
                    "--max-turns",
                    str(per_attempt_turns),
                    "--permission-mode",
                    "acceptEdits",
                    "--allowedTools",
                    "Read,Edit,Write,Bash(git status),Bash(git diff),Bash(ls),Bash(cat),Bash(grep),Bash(find),Bash(rg),WebSearch,WebFetch",
                    "--output-format",
                    "text",
                ],
                cwd=wt,
                env=env,
                timeout=timeout_s,
                stdin_data=prompt,
                progress_cb=progress_cb,
                attempt_no=attempt_no,
                attempt_limit=max_attempts,
                model_name=model_name,
                auth_slot=_mask_token(auth_token),
            )
            log_tail = _combine_log_tail(cc.stdout, cc.stderr)
            last_tail = log_tail
            failure_kind = _classify_agent_failure(cc.returncode, log_tail)
            attempt = {
                "model": model_name,
                "auth_slot": _mask_token(auth_token),
                "ready": True,
                "returncode": cc.returncode,
                "failure_kind": failure_kind,
                "timeout_s": timeout_s,
            }
            attempts.append(attempt)
            _progress(progress_cb, attempts=attempts, log_tail=log_tail)
            if failure_kind == "success":
                return cc, log_tail, attempts
            if failure_kind == "auth":
                next_auth_index = _next_ready_auth_token(
                    tokens,
                    auth_index + 1,
                    attempts,
                    progress_cb,
                    detail="Primary Claude gateway auth failed; checking backup token.",
                    started=started,
                    total_budget_seconds=total_budget_seconds,
                )
                if next_auth_index is not None:
                    auth_index = next_auth_index
                    continue
                break
            if failure_kind == "transient":
                model_index += 1
                continue
            break
        except subprocess.TimeoutExpired as e:
            tail = _combine_log_tail(
                e.output if isinstance(e.output, str) else "",
                e.stderr if isinstance(e.stderr, str) else "",
            )
            if not tail:
                tail = f"timeout after {timeout_s}s: {type(e).__name__}: {e}"
            last_tail = tail
            attempts.append(
                {
                    "model": model_name,
                    "auth_slot": _mask_token(auth_token),
                    "ready": True,
                    "timeout": timeout_s,
                    "failure_kind": "transient",
                }
            )
            _progress(progress_cb, attempts=attempts, log_tail=tail)
            model_index += 1
            continue
        except Exception as e:
            tail = f"{type(e).__name__}: {e}"
            last_tail = tail
            attempts.append(
                {
                    "model": model_name,
                    "auth_slot": _mask_token(auth_token),
                    "ready": True,
                    "error": tail[:200],
                    "failure_kind": "fatal",
                }
            )
            _progress(progress_cb, attempts=attempts, log_tail=tail)
            break

    return None, last_tail, attempts


def _classify_agent_failure(returncode: int, log_tail: str) -> str:
    if returncode == 0:
        return "success"
    txt = (log_tail or "").lower()
    auth_signals = (
        "401",
        "403",
        "unauthorized",
        "forbidden",
        "authentication",
        "invalid api key",
        "invalid bearer",
        "bad bearer",
    )
    transient_signals = (
        "timed out",
        "timeout",
        "empty or malformed response",
        "api error",
        "429",
        "502",
        "503",
        "504",
        "connection reset",
        "temporarily unavailable",
        "upstream",
    )
    if any(sig in txt for sig in auth_signals):
        return "auth"
    if any(sig in txt for sig in transient_signals):
        return "transient"
    return "fatal"


def _mask_token(tok: str) -> str:
    if not tok:
        return "none"
    if tok == DEVBOX_TOKEN:
        return "default"
    return f"slot:{tok[-6:]}"


def _first_ready_auth_token(
    tokens: list[str],
    attempts: list[dict],
    progress_cb: Optional[Callable[[dict], None]],
    detail: str,
    started: float,
    total_budget_seconds: int,
) -> Optional[int]:
    return _next_ready_auth_token(
        tokens,
        0,
        attempts,
        progress_cb,
        detail,
        started=started,
        total_budget_seconds=total_budget_seconds,
    )


def _next_ready_auth_token(
    tokens: list[str],
    start_index: int,
    attempts: list[dict],
    progress_cb: Optional[Callable[[dict], None]],
    detail: str,
    started: float,
    total_budget_seconds: int,
) -> Optional[int]:
    for idx in range(start_index, len(tokens)):
        token = tokens[idx]
        remaining = max(0, total_budget_seconds - int(time.monotonic() - started))
        if remaining < 5:
            attempts.append(
                {
                    "auth_slot": _mask_token(token),
                    "ready": False,
                    "error": "budget exhausted before gateway probe",
                }
            )
            _progress(progress_cb, attempts=attempts)
            return None
        _progress(
            progress_cb,
            stage="checking_gateway",
            detail=f"{detail} Slot {_mask_token(token)} (budget left {remaining}s).",
            attempts=attempts,
            progress={"auth_slot": _mask_token(token), "budget_remaining_s": remaining},
        )
        fcc_err = _wait_fcc_ready(timeout_s=min(25, remaining), auth_token=token)
        if not fcc_err:
            return idx
        attempts.append(
            {
                "auth_slot": _mask_token(token),
                "ready": False,
                "error": fcc_err[:200],
            }
        )
        _progress(progress_cb, attempts=attempts)
    return None


def _run_claude_attempt(
    cmd: list[str],
    cwd: Path,
    env: dict,
    timeout: int,
    stdin_data: str,
    progress_cb: Optional[Callable[[dict], None]],
    attempt_no: int,
    attempt_limit: int,
    model_name: str,
    auth_slot: str,
) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None
    proc.stdin.write(stdin_data)
    proc.stdin.close()

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def _reader(pipe, store: list[str]) -> None:
        try:
            for line in iter(pipe.readline, ""):
                store.append(line)
        finally:
            pipe.close()

    stdout_thread = threading.Thread(target=_reader, args=(proc.stdout, stdout_lines), daemon=True)
    stderr_thread = threading.Thread(target=_reader, args=(proc.stderr, stderr_lines), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    started = time.monotonic()
    last_update = 0.0
    while True:
        rc = proc.poll()
        elapsed = int(time.monotonic() - started)
        now = time.monotonic()
        if progress_cb and now - last_update >= PROGRESS_UPDATE_SECONDS:
            _progress(
                progress_cb,
                stage="running_agent",
                detail=(
                    f"Claude attempt {attempt_no}/{attempt_limit} is still running on {model_name} "
                    f"({elapsed}s elapsed)."
                ),
                log_tail=_combine_log_tail("".join(stdout_lines), "".join(stderr_lines)),
                progress={
                    "attempt": attempt_no,
                    "attempt_limit": attempt_limit,
                    "model": model_name,
                    "auth_slot": auth_slot,
                    "attempt_elapsed_s": elapsed,
                    "timeout_s": timeout,
                },
            )
            last_update = now
        if rc is not None:
            break
        if elapsed >= timeout:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            raise subprocess.TimeoutExpired(
                cmd,
                timeout,
                output="".join(stdout_lines),
                stderr="".join(stderr_lines),
            )
        time.sleep(1)

    stdout_thread.join(timeout=2)
    stderr_thread.join(timeout=2)
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
    )


def _combine_log_tail(stdout_text: str, stderr_text: str) -> str:
    left = (stdout_text or "")[-1000:]
    right = (stderr_text or "")[-800:]
    if left and right:
        return f"{left}\n---STDERR---\n{right}"[-1800:]
    return (left or right)[-1800:]


def _progress(
    progress_cb: Optional[Callable[[dict], None]],
    *,
    stage: Optional[str] = None,
    detail: Optional[str] = None,
    log_tail: Optional[str] = None,
    attempts: Optional[list[dict]] = None,
    progress: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    if progress_cb is None:
        return
    payload: dict = {}
    progress_block = dict(progress or {})
    if stage is not None:
        progress_block["stage"] = stage
    if detail is not None:
        progress_block["detail"] = detail
    if progress_block:
        payload["progress"] = progress_block
    if log_tail is not None:
        payload["log_tail"] = log_tail[-STATUS_LOG_TAIL_CHARS:]
    if attempts is not None:
        payload["attempts"] = attempts
    if error is not None:
        payload["error"] = error
    if payload:
        progress_cb(payload)
