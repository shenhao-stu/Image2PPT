"""System routes — version, manual update trigger, WebSocket."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from pydantic import BaseModel

from ..config import get_settings
from .. import github_sync, queue as job_queue, runtime_settings, sandbox
from ..db import SessionLocal
from ..deps import get_current_user, require_admin
from ..models import User
from ..schemas import VersionOut
from ..security import decode_token
from ..ws import broker

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/version", response_model=VersionOut)
def version(_: User = Depends(get_current_user)) -> VersionOut:
    s = get_settings()
    snap = github_sync.snapshot()
    return VersionOut(
        commit=snap.get("commit", ""),
        short_commit=snap.get("short_commit", ""),
        behind=snap.get("behind", 0),
        ahead=snap.get("ahead", 0),
        branch=snap.get("branch", s.git_branch),
        remote_url=s.github_repo_url,
        auto_update=runtime_settings.get_auto_update(),
        updating=bool(snap.get("updating")),
        last_check=snap.get("last_check"),
        sandbox_backend=sandbox.active_backend(),
        sandbox_allow_network=s.sandbox_allow_network,
    )


class AutoUpdateIn(BaseModel):
    enabled: bool


@router.post("/auto-update")
def set_auto_update(payload: AutoUpdateIn, _: User = Depends(require_admin)):
    runtime_settings.set_auto_update(payload.enabled)
    return {"auto_update": runtime_settings.get_auto_update()}


@router.post("/update", status_code=202)
async def trigger_update(_: User = Depends(require_admin)):
    if not github_sync.snapshot().get("behind"):
        raise HTTPException(409, "Already up to date")
    # Fire-and-forget — _try_update reschedules itself via execv.
    import asyncio
    asyncio.create_task(github_sync._try_update())
    return {"started": True}


@router.post("/sweep-orphans")
def sweep_orphans(_: User = Depends(require_admin)):
    """Admin: trigger one orphan-dir sweep immediately. Useful after
    incidents that may have left disk and DB out of sync (manual DB
    edits, crashed delete flows). Returns the number of dirs removed."""
    swept = job_queue.sweep_orphan_dirs()
    return {"swept": swept}


ws_router = APIRouter()


@ws_router.websocket("/ws/jobs")
async def jobs_ws(websocket: WebSocket):
    token = websocket.query_params.get("token")
    sub = decode_token(token) if token else None
    if not sub:
        await websocket.close(code=4401)
        return
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.username == sub).one_or_none()
    finally:
        db.close()
    if not user:
        await websocket.close(code=4401)
        return
    client = await broker.connect(websocket, user.id, bool(user.is_admin))
    try:
        while True:
            # We don't need any client → server messages, but reading
            # keeps the connection alive and surfaces disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        await broker.disconnect(client)
