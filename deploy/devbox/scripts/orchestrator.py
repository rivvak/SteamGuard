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

import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

# Import path-guard rules from the repo (baked into the checkout)
REPO = Path("/home/sgagent/SteamGuard")
sys.path.insert(0, str(REPO))
from ai.guards.diff_check import classify_diff  # noqa: E402

DEVBOX_TOKEN = os.environ["DEVBOX_TOKEN"]
FCC_URL = os.environ.get("FCC_URL", "http://127.0.0.1:8082")
MODEL = os.environ.get("AI_DEVBOX_MODEL", "z-ai/glm-5.2")
WORK_ROOT = Path(os.environ.get("SG_WORK_ROOT", "/var/lib/sg-devbox/work"))
REPO_SLUG = os.environ.get("REPO_SLUG", "rivvak/SteamGuard")
OPEN_PR = Path("/opt/sg-devbox/open-pr.sh")

app = FastAPI(title="sg-devbox orchestrator")

# Register /create + /create/status/{sid} routes (Phase 3, async, containerised).
# Kept in a separate module so this file stays focused on the /session flow.
from create_handler import router as _create_router  # noqa: E402

app.include_router(_create_router)


class SessionBody(BaseModel):
    task: str = Field(min_length=1, max_length=8000)
    source: str = Field(default="develop", pattern="^(develop|heal)$")
    ref: str = Field(default="main")
    context: Optional[str] = None  # e.g. failure log for heal
    initiator: Optional[str] = None  # discord user id or GH run id


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
    wt = WORK_ROOT / sid
    WORK_ROOT.mkdir(parents=True, exist_ok=True)

    # 1) fresh worktree from origin/<ref>
    up = _run(["git", "fetch", "origin", body.ref], REPO)
    if up.returncode != 0:
        return _error(sid, "git fetch failed", up.stderr)
    add = _run(["git", "worktree", "add", "-f", str(wt), f"origin/{body.ref}"], REPO)
    if add.returncode != 0:
        return _error(sid, "git worktree add failed", add.stderr)

    # 2) build the prompt (Claude Code reads from stdin in -p mode)
    prompt = (
        f"# SteamGuard AI task ({body.source})\n\n"
        f"Session id: {sid}\n"
        f"Initiator: {body.initiator or 'unknown'}\n\n"
        "## Rules\n"
        "- Never touch: auth/, license/entitlement/hwid/cert/pinning code, "
        ".env*, *.pem/*.key, .github/workflows/**, Dockerfiles, build scripts, hashes.txt.\n"
        "- Prefer minimal, testable changes. Edit files in the current working directory.\n"
        "- After you finish, exit — do not commit; the orchestrator handles git.\n\n"
        "## Task\n\n"
        + body.task
        + (f"\n\n## Context\n\n{body.context}\n" if body.context else "")
    )
    # Also drop a copy on disk for debugging.
    (wt / ".sg-task.md").write_text(prompt)

    # 3) invoke Claude Code in headless print mode via FCC proxy.
    # Env mirrors what `fcc-claude` sets: strip ANTHROPIC_* from parent, then
    # add BASE_URL, AUTH_TOKEN, gateway model discovery, and compact window.
    # FCC routes to the NIM model configured in its Admin UI (MODEL setting) —
    # do NOT set ANTHROPIC_MODEL here; fcc-claude explicitly strips it.
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    env = {
        **base_env,
        "ANTHROPIC_BASE_URL": FCC_URL,
        "ANTHROPIC_AUTH_TOKEN": DEVBOX_TOKEN,
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "190000",
    }
    # Use --permission-mode acceptEdits so Claude Code auto-accepts file edits
    # in this sandboxed worktree without requiring an interactive TTY. The
    # devbox already isolates: rootless subprocess as `sgagent`, dedicated VM,
    # narrow allowedTools, and a path-guard classifier on the resulting diff.
    cc = _run(
        [
            "claude",
            "-p",
            "--max-turns",
            "30",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            "Read,Edit,Write,Bash(git status),Bash(git diff),Bash(ls),Bash(cat),Bash(grep),Bash(find),Bash(rg)",
            "--output-format",
            "text",
        ],
        cwd=wt,
        env=env,
        timeout=1200,
        stdin_data=prompt,
    )
    log_tail = (cc.stdout[-1500:] + "\n---STDERR---\n" + cc.stderr[-1500:])

    # 4) classify diff
    diff = _run(["git", "diff", "--name-only"], wt)
    files = [ln for ln in diff.stdout.splitlines() if ln.strip()]
    if not files:
        _cleanup(wt)
        return {"status": "empty", "session_id": sid, "log_tail": log_tail}

    cls = classify_diff(files)

    if cls.classification == "denied":
        _cleanup(wt)
        return {
            "status": "refused",
            "session_id": sid,
            "denied_files": cls.denied_files,
            "log_tail": log_tail,
        }

    # 5) stage + commit
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
        push = _run(["git", "push", "origin", f"HEAD:{body.ref}"], wt)
        if push.returncode != 0:
            _cleanup(wt)
            return _error(sid, "git push (direct) failed", push.stderr)
        sha = _run(["git", "rev-parse", "HEAD"], wt).stdout.strip()
        _cleanup(wt)
        return {
            "status": "committed",
            "session_id": sid,
            "commit_sha": sha,
            "commit_url": f"https://github.com/{REPO_SLUG}/commit/{sha}",
            "files": files,
        }

    # else pr
    branch = f"sg-heal/{body.source}-{sid}"
    _run(["git", "branch", "-M", branch], wt)
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
    return {
        "status": "pr_opened",
        "session_id": sid,
        "pr_url": pr_url,
        "files": files,
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


def _error(sid: str, msg: str, tail: str):
    return {"status": "error", "session_id": sid, "error": msg, "log_tail": tail[-1500:]}
