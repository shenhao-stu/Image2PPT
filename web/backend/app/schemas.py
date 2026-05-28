"""Pydantic models for API I/O."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class LoginIn(BaseModel):
    username: str
    password: str
    # Optional PoW fields — required only when DECKWEAVER_POW_REQUIRED=true.
    # The client first GETs /api/auth/pow to obtain the challenge string.
    pow_challenge: str | None = None
    pow_nonce: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    is_admin: bool


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    is_admin: bool


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source_filename: str
    source_kind: str
    mode: Literal["full", "text-only"]
    status: Literal["queued", "running", "done", "failed", "canceled"]
    page_count: int
    current_page: int
    progress_pct: int
    duration_seconds: int
    error_msg: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    queue_position: int = 0
    eta_seconds: int = 0


class JobLogOut(BaseModel):
    id: str
    log_tail: str


class VersionOut(BaseModel):
    commit: str
    short_commit: str
    behind: int
    ahead: int
    branch: str
    remote_url: str
    auto_update: bool
    updating: bool
    last_check: datetime | None
    sandbox_backend: str = "none"
    sandbox_allow_network: bool = True


class UserCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class BulkDeleteIn(BaseModel):
    """Body for POST /api/jobs/bulk-delete."""
    ids: list[str]
    # When true, running jobs are SIGTERMed first and then deleted in
    # the same call (so the user doesn't have to cancel+delete twice).
    force: bool = False


class BulkDeleteOut(BaseModel):
    """Result of POST /api/jobs/bulk-delete."""
    deleted: list[str]
    skipped: list[dict]  # [{id, reason}]
    storage_freed_mb: int = 0
