"""HTTP routes for the Discord /memory command group.

Called by the license-server (via IAP tunnel) which is itself called by the
`/memory` Discord cog. Bearer-authed with DEVBOX_TOKEN like every other
orchestrator route.

Endpoints
---------
GET   /memory/show/{scope}/{scope_id}         -> {markdown, entry_count}
POST  /memory/clear/{scope}/{scope_id}        -> {ok, removed}
POST  /memory/forget/{scope}/{scope_id}       -> body {entry_id}. {ok}
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

import memory_store

DEVBOX_TOKEN = os.environ["DEVBOX_TOKEN"]

router = APIRouter(prefix="/memory")


def _auth(header: Optional[str]) -> None:
    if not header or not header.startswith("Bearer "):
        raise HTTPException(401, "missing bearer")
    tok = header.split(" ", 1)[1].strip()
    if len(tok) != len(DEVBOX_TOKEN):
        raise HTTPException(401, "bad bearer")
    r = 0
    for x, y in zip(tok.encode(), DEVBOX_TOKEN.encode()):
        r |= x ^ y
    if r != 0:
        raise HTTPException(401, "bad bearer")


class ForgetBody(BaseModel):
    entry_id: str = Field(min_length=6, max_length=32)


def _valid_scope(scope: str) -> None:
    if scope not in ("user", "channel"):
        raise HTTPException(400, "scope must be 'user' or 'channel'")


@router.get("/show/{scope}/{scope_id}")
def show(scope: str, scope_id: str, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    _valid_scope(scope)
    blob = memory_store.load(scope, scope_id)  # type: ignore[arg-type]
    md = memory_store.render_show(scope, scope_id)  # type: ignore[arg-type]
    return {
        "scope": scope,
        "scope_id": scope_id,
        "entry_count": len(blob.entries),
        "has_summary": bool(blob.rolling_summary),
        "markdown": md,
    }


@router.post("/clear/{scope}/{scope_id}")
def clear(scope: str, scope_id: str, authorization: Optional[str] = Header(default=None)):
    _auth(authorization)
    _valid_scope(scope)
    ok = memory_store.clear(scope, scope_id)  # type: ignore[arg-type]
    return {"ok": ok, "scope": scope, "scope_id": scope_id}


@router.post("/forget/{scope}/{scope_id}")
def forget(
    scope: str,
    scope_id: str,
    body: ForgetBody,
    authorization: Optional[str] = Header(default=None),
):
    _auth(authorization)
    _valid_scope(scope)
    ok = memory_store.forget_entry(scope, scope_id, body.entry_id)  # type: ignore[arg-type]
    if not ok:
        raise HTTPException(404, f"entry_id {body.entry_id} not found in {scope}:{scope_id}")
    return {"ok": True, "scope": scope, "scope_id": scope_id, "removed": body.entry_id}
