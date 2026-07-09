"""Session memory for /create + /develop — GCS-backed, per-user + per-channel.

Design goals
------------
* **Continuity across commands.** After running `/create build me a hello.py`,
  the user can immediately run `/develop wire it into the server` and the agent
  will read prior context and know what "it" refers to.
* **Per-user + per-channel.** Each command remembers who ran it (user memory)
  and where they ran it (channel memory). Both scopes are merged into the
  prompt preamble.
* **Bounded size.** Each scope stores the last 20 raw entries + a running
  summary of anything older. Preamble injected into the sandbox is capped at
  ~4KB so the working prompt stays reasonable.
* **No new infra.** Persists as JSON blobs under
  `gs://steamguard-create-artifacts/memory/{scope}/{id}.json`.
* **Best-effort.** GCS outage never blocks a command — memory just skips.

Wire diagram
------------

    Discord /create ─┐
                     │ prompt + user_id + channel_id
                     ▼
    orchestrator /create ─── memory_store.build_preamble()
                     │              │
                     │              └── GCS read users/{uid}.json + channels/{cid}.json
                     ▼
    docker run sg-sandbox                (PRIOR_CONTEXT.md injected via bind mount)
                     │
                     └── on finish → memory_store.record_completion()
                                            │
                                            └── GCS write both blobs
                                                (LLM-summarizes if >20 entries)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Literal, Optional

MEMORY_BUCKET = os.environ.get("SG_MEMORY_BUCKET", "steamguard-create-artifacts")
MEMORY_PREFIX = os.environ.get("SG_MEMORY_PREFIX", "memory")
MAX_RAW_ENTRIES = int(os.environ.get("SG_MEMORY_MAX_RAW", "20"))
PREAMBLE_CHAR_BUDGET = int(os.environ.get("SG_MEMORY_PREAMBLE_BUDGET", "4000"))

# NIM summarizer config — one lightweight completion call per session end.
NIM_BASE_URL = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
NIM_KEY = os.environ.get("NVIDIA_NIM_API_KEY", "")
SUMMARY_MODEL = os.environ.get("SG_MEMORY_SUMMARY_MODEL", "z-ai/glm-5.2")

Scope = Literal["user", "channel"]


@dataclass
class Entry:
    """One recorded command turn."""

    entry_id: str
    ts: int                       # unix seconds
    session_id: str               # /create session id (may be blank for /develop)
    command: str                  # "create" | "develop"
    prompt: str                   # truncated to 400 chars for display, full stored
    prompt_full: str
    initiator: str                # discord user id
    channel_id: str               # discord channel id
    artifact_url: Optional[str] = None
    status: str = "unknown"       # "finished" | "error" | "committed" | "pr_opened" | ...
    summary: Optional[str] = None # populated by the summarizer on completion


@dataclass
class MemoryBlob:
    scope: Scope
    scope_id: str
    entries: list[Entry] = field(default_factory=list)
    rolling_summary: str = ""     # LLM summary of entries older than MAX_RAW_ENTRIES

    @classmethod
    def empty(cls, scope: Scope, scope_id: str) -> "MemoryBlob":
        return cls(scope=scope, scope_id=scope_id)

    def to_json(self) -> dict:
        return {
            "scope": self.scope,
            "scope_id": self.scope_id,
            "entries": [asdict(e) for e in self.entries],
            "rolling_summary": self.rolling_summary,
            "updated_at": int(time.time()),
        }

    @classmethod
    def from_json(cls, data: dict, scope: Scope, scope_id: str) -> "MemoryBlob":
        entries = [Entry(**e) for e in data.get("entries", [])]
        return cls(
            scope=scope,
            scope_id=scope_id,
            entries=entries,
            rolling_summary=data.get("rolling_summary", ""),
        )


# ─── GCS I/O ────────────────────────────────────────────────────────────────


def _gcs_path(scope: Scope, scope_id: str) -> str:
    # scope_id is a discord snowflake — numeric string, safe to embed
    safe = "".join(c for c in scope_id if c.isalnum())[:32] or "unknown"
    return f"gs://{MEMORY_BUCKET}/{MEMORY_PREFIX}/{scope}s/{safe}.json"


def _gcs_read(path: str) -> Optional[dict]:
    """Read JSON from GCS. Returns None if not found (or on any error)."""
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=True) as tmp:
        r = subprocess.run(
            ["gcloud", "storage", "cp", path, tmp.name, "--quiet"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0:
            return None  # not found or transient — caller treats as empty
        try:
            tmp.seek(0)
            return json.loads(tmp.read())
        except Exception:
            return None


def _gcs_write(path: str, data: dict) -> bool:
    """Write JSON to GCS. Returns True on success. Best-effort."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(data, tmp)
        tmp_path = tmp.name
    try:
        r = subprocess.run(
            ["gcloud", "storage", "cp", tmp_path, path, "--quiet"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.returncode == 0
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _gcs_rm(path: str) -> bool:
    r = subprocess.run(
        ["gcloud", "storage", "rm", path, "--quiet"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return r.returncode == 0


# ─── public API ─────────────────────────────────────────────────────────────


def load(scope: Scope, scope_id: str) -> MemoryBlob:
    """Load a memory blob for the given scope. Returns an empty blob if none exists."""
    path = _gcs_path(scope, scope_id)
    raw = _gcs_read(path)
    if not raw:
        return MemoryBlob.empty(scope, scope_id)
    return MemoryBlob.from_json(raw, scope, scope_id)


def save(blob: MemoryBlob) -> bool:
    path = _gcs_path(blob.scope, blob.scope_id)
    return _gcs_write(path, blob.to_json())


def clear(scope: Scope, scope_id: str) -> bool:
    return _gcs_rm(_gcs_path(scope, scope_id))


def forget_entry(scope: Scope, scope_id: str, entry_id: str) -> bool:
    blob = load(scope, scope_id)
    before = len(blob.entries)
    blob.entries = [e for e in blob.entries if e.entry_id != entry_id]
    if len(blob.entries) == before:
        return False
    return save(blob)


def append_and_maybe_summarize(
    scope: Scope,
    scope_id: str,
    entry: Entry,
) -> MemoryBlob:
    """Append `entry`. If we're over MAX_RAW_ENTRIES, roll the oldest into the summary."""
    blob = load(scope, scope_id)
    blob.entries.append(entry)

    if len(blob.entries) > MAX_RAW_ENTRIES:
        # Take everything past the cap, summarize, prepend to rolling_summary.
        overflow = blob.entries[:-MAX_RAW_ENTRIES]
        blob.entries = blob.entries[-MAX_RAW_ENTRIES:]
        addition = _summarize_entries(overflow)
        if addition:
            if blob.rolling_summary:
                blob.rolling_summary = _summarize_summary_merge(
                    blob.rolling_summary, addition
                )
            else:
                blob.rolling_summary = addition

    save(blob)
    return blob


def build_preamble(
    user_id: str,
    channel_id: str,
    budget_chars: int = PREAMBLE_CHAR_BUDGET,
) -> str:
    """Produce the PRIOR_CONTEXT.md content injected into the sandbox /workspace.

    Merges user + channel memory chronologically, most recent first, capped at
    `budget_chars`. Returns empty string when both scopes are empty.
    """
    user_blob = load("user", user_id) if user_id else MemoryBlob.empty("user", "")
    chan_blob = load("channel", channel_id) if channel_id else MemoryBlob.empty("channel", "")

    if not user_blob.entries and not chan_blob.entries \
       and not user_blob.rolling_summary and not chan_blob.rolling_summary:
        return ""

    parts: list[str] = [
        "# Prior context for this Discord user",
        "",
        "You have run commands in this Discord workspace before. Read this file",
        "carefully — the current /create or /develop request may refer back to",
        "earlier prompts (e.g. 'add tests for what you just built', 'wire it into",
        "the previous script', 'like last time'). Use file references from the",
        "artifact URLs below when the user says 'the zip you gave me' etc.",
        "",
    ]

    if user_blob.rolling_summary:
        parts.append("## Long-term summary (user)")
        parts.append(user_blob.rolling_summary.strip())
        parts.append("")

    if chan_blob.rolling_summary:
        parts.append("## Long-term summary (channel)")
        parts.append(chan_blob.rolling_summary.strip())
        parts.append("")

    # Merge recent entries: dedupe by entry_id, sort desc by ts.
    seen: set[str] = set()
    merged: list[Entry] = []
    for e in list(user_blob.entries) + list(chan_blob.entries):
        if e.entry_id in seen:
            continue
        seen.add(e.entry_id)
        merged.append(e)
    merged.sort(key=lambda e: e.ts, reverse=True)

    parts.append("## Recent commands (newest first)")
    parts.append("")

    for e in merged:
        block = _render_entry_md(e)
        # Stop early if we're about to blow the budget
        if sum(len(p) for p in parts) + len(block) > budget_chars:
            parts.append("_(older entries elided; see long-term summary above)_")
            break
        parts.append(block)

    return "\n".join(parts).strip() + "\n"


def _render_entry_md(e: Entry) -> str:
    ts_iso = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(e.ts))
    lines = [
        f"### {ts_iso} — /{e.command}  ({e.status})",
        f"- entry_id: `{e.entry_id}`",
        f"- session:  `{e.session_id}`" if e.session_id else "",
        f"- prompt:   {e.prompt_full[:400]}",
    ]
    if e.artifact_url:
        lines.append(f"- artifact: {e.artifact_url}")
    if e.summary:
        lines.append(f"- summary:  {e.summary}")
    lines.append("")
    return "\n".join(l for l in lines if l)


# ─── summarizer ─────────────────────────────────────────────────────────────


def _summarize_entries(entries: Iterable[Entry]) -> str:
    """Ask NIM to produce a 2-3 sentence recap of overflow entries."""
    entries = list(entries)
    if not entries or not NIM_KEY:
        return ""
    prompt = (
        "You are summarizing a Discord user's older AI agent commands to keep as "
        "long-term context. Produce a 2-4 sentence recap that captures what they "
        "built, what worked, and any recurring themes. Refer to specific artifacts "
        "when present. Do not use bullet points.\n\n"
        "Entries:\n"
        + "\n---\n".join(
            f"[{time.strftime('%Y-%m-%d', time.gmtime(e.ts))}] /{e.command} "
            f"({e.status}): {e.prompt_full[:300]}"
            + (f" → artifact {e.artifact_url}" if e.artifact_url else "")
            + (f" | prev summary: {e.summary}" if e.summary else "")
            for e in entries
        )
    )
    return _nim_completion(prompt, max_tokens=180)


def _summarize_summary_merge(old: str, new: str) -> str:
    """Merge two summaries into one 3-5 sentence blurb via NIM."""
    if not NIM_KEY:
        return (old + " " + new)[:2000]
    prompt = (
        "Merge these two running summaries of a user's past AI agent work into "
        "one cohesive 3-5 sentence summary. Preserve concrete project names, "
        "file names, and artifact references.\n\n"
        f"OLD SUMMARY:\n{old}\n\nNEW ADDITION:\n{new}"
    )
    return _nim_completion(prompt, max_tokens=250) or (old + " " + new)[:2000]


def _summarize_session(prompt: str, log_tail: str, status: str) -> str:
    """One-line-ish recap of a single session, stored on the Entry."""
    if not NIM_KEY:
        return ""
    q = (
        "Summarize this AI agent session in ONE sentence (max 40 words). "
        "Focus on: what the user asked for, what got produced, and whether "
        "it succeeded. No preamble.\n\n"
        f"Status: {status}\n"
        f"User prompt: {prompt[:800]}\n"
        f"Agent log tail: {log_tail[-1200:] if log_tail else '(none)'}"
    )
    return _nim_completion(q, max_tokens=90)


def _nim_completion(prompt: str, max_tokens: int = 200) -> str:
    """Direct NIM chat completion call. Returns empty string on failure."""
    if not NIM_KEY:
        return ""
    try:
        import httpx
        r = httpx.post(
            f"{NIM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {NIM_KEY}"},
            json={
                "model": SUMMARY_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
            timeout=30.0,
        )
        if r.status_code >= 300:
            return ""
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return ""


# ─── convenience: record a completed session ────────────────────────────────


def record_completion(
    *,
    command: str,
    session_id: str,
    prompt: str,
    initiator: str,
    channel_id: str,
    status: str,
    artifact_url: Optional[str] = None,
    log_tail: Optional[str] = None,
) -> Entry:
    """Called by the orchestrator after a /create or /develop finishes.

    Writes the entry into BOTH per-user and per-channel blobs. Idempotent per
    session_id: if the same session_id already exists in a scope, it's replaced
    rather than duplicated.
    """
    summary = _summarize_session(prompt, log_tail or "", status)
    entry = Entry(
        entry_id=uuid.uuid4().hex[:12],
        ts=int(time.time()),
        session_id=session_id or "",
        command=command,
        prompt=prompt[:400],
        prompt_full=prompt[:4000],
        initiator=initiator,
        channel_id=channel_id,
        artifact_url=artifact_url,
        status=status,
        summary=summary or None,
    )

    for scope, sid in (("user", initiator), ("channel", channel_id)):
        if not sid:
            continue
        blob = load(scope, sid)
        # dedupe by session_id (if we already recorded this session for this scope, skip)
        if session_id and any(e.session_id == session_id for e in blob.entries):
            continue
        blob.entries.append(entry)
        if len(blob.entries) > MAX_RAW_ENTRIES:
            overflow = blob.entries[:-MAX_RAW_ENTRIES]
            blob.entries = blob.entries[-MAX_RAW_ENTRIES:]
            addition = _summarize_entries(overflow)
            if addition:
                if blob.rolling_summary:
                    blob.rolling_summary = _summarize_summary_merge(
                        blob.rolling_summary, addition
                    )
                else:
                    blob.rolling_summary = addition
        save(blob)

    return entry


def render_show(scope: Scope, scope_id: str) -> str:
    """Human-readable dump for /memory show."""
    blob = load(scope, scope_id)
    if not blob.entries and not blob.rolling_summary:
        return f"_(no memory in {scope}:{scope_id})_"
    parts = [f"**Memory — {scope} `{scope_id}`**  ({len(blob.entries)} recent entries)"]
    if blob.rolling_summary:
        parts.append("")
        parts.append("**Long-term summary:**")
        parts.append(blob.rolling_summary[:1000])
    parts.append("")
    parts.append("**Recent entries:**")
    for e in sorted(blob.entries, key=lambda e: e.ts, reverse=True)[:15]:
        ts = time.strftime("%m-%d %H:%M", time.gmtime(e.ts))
        line = f"- `{e.entry_id}` [{ts}] /{e.command} — {e.prompt[:80]}"
        if e.status:
            line += f"  _({e.status})_"
        parts.append(line)
    return "\n".join(parts)
