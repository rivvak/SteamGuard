"""POST /ai/ask — RAG-backed Q&A for the SteamGuard loader.

Auth model (matches server/main.py `/verify`):
    body: {"key": "...", "hwid": "...", "sig": "HMAC(SECRET_KEY, f'{key}:{hwid}')",
           "question": "..."}
    Server re-signs (key, hwid) and rejects on mismatch. This reuses the
    existing HMAC scheme rather than introducing a new token type.

Per-user rate limit: 30 req/hour keyed by SHA256(key). Enforced via Firestore
counter doc so it survives Cloud Run instance rotation.

The feature is gated by env `AI_ASK_ENABLED=true`. When disabled, the endpoint
returns 503 and the loader hides the chat modal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from google.cloud import firestore
from pydantic import BaseModel, Field

from ai.client import get_llm
from ai.rag.retrieve import format_context, retrieve

router = APIRouter(prefix="/ai", tags=["ai"])

_SECRET_KEY = os.environ["SECRET_KEY"]  # same secret used by /verify
_ENABLED = os.environ.get("AI_ASK_ENABLED", "false").lower() == "true"
_MODEL = os.environ.get("AI_ASK_MODEL", "sg-rag")
_RATE_LIMIT_PER_HOUR = int(os.environ.get("AI_ASK_RATE_PER_HOUR", "30"))
_PROMPT_PATH = Path(__file__).resolve().parent.parent.parent / "ai" / "prompts" / "ask_system.md"

_db = firestore.Client()


class AskBody(BaseModel):
    key: str
    hwid: str
    sig: str
    question: str = Field(min_length=1, max_length=2000)


def _verify_sig(key: str, hwid: str, sig: str) -> bool:
    expected = hmac.new(
        _SECRET_KEY.encode(),
        f"{key}:{hwid}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig)


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _check_rate_limit(key: str) -> None:
    """Firestore-backed sliding hour bucket. Raises 429 on breach."""
    now = int(time.time())
    bucket = now // 3600
    doc_ref = _db.collection("ai_ask_rate").document(f"{_key_hash(key)}_{bucket}")
    snap = doc_ref.get()
    count = (snap.to_dict() or {}).get("count", 0) if snap.exists else 0
    if count >= _RATE_LIMIT_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit: {_RATE_LIMIT_PER_HOUR}/hour",
        )
    doc_ref.set(
        {"count": count + 1, "expires_at": (bucket + 2) * 3600},
        merge=True,
    )


def _load_system_prompt(context: str) -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8").replace("{context}", context)


@router.post("/ask")
async def ask(body: AskBody, request: Request) -> StreamingResponse:
    if not _ENABLED:
        raise HTTPException(status_code=503, detail="AI assistant is temporarily disabled")

    if not _verify_sig(body.key, body.hwid, body.sig):
        raise HTTPException(status_code=401, detail="invalid signature")

    _check_rate_limit(body.key)

    passages = retrieve(body.question, k=4)
    system = _load_system_prompt(format_context(passages))
    llm = get_llm()

    async def stream() -> AsyncIterator[bytes]:
        completion = llm.chat.completions.create(
            model=_MODEL,
            stream=True,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": body.question},
            ],
            temperature=0.2,
            max_tokens=800,
        )
        # Server-sent events, one JSON delta per line
        for chunk in completion:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield f"data: {json.dumps({'delta': delta})}\n\n".encode()
        # Trailing sources block for the client to render
        yield (
            f"data: {json.dumps({'sources': [p.source for p in passages]})}\n\n"
        ).encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
