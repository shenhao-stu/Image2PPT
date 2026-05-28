"""Auth routes — login, whoami, admin user creation."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_db, get_current_user, require_admin
from ..models import User
from ..ratelimit import RateLimiter
from ..schemas import LoginIn, TokenOut, UserOut, UserCreate
from ..security import hash_password, issue_token, verify_password
from .. import pow_challenge

router = APIRouter(prefix="/api/auth", tags=["auth"])

_login_limiter = RateLimiter(get_settings().login_rate_per_min)


@router.get("/pow")
def pow_issue() -> dict:
    """Issue a fresh PoW challenge. The client must solve it before
    /api/auth/login will be accepted."""
    return pow_challenge.issue()


def _client_ip(request: Request) -> str:
    # Honor X-Forwarded-For when behind a reverse proxy. Operators must
    # set this only after a trusted proxy that overwrites the header.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=TokenOut)
def login(
    payload: LoginIn,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenOut:
    ip = _client_ip(request)
    if not _login_limiter.allow(f"login:{ip}"):
        retry = _login_limiter.retry_after(f"login:{ip}")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many login attempts, retry in {retry}s",
            headers={"Retry-After": str(retry)},
        )
    # PoW gate (skipped only if explicitly disabled via env). We verify
    # BEFORE the password compare so attackers can't burn bcrypt cycles
    # for free.
    import os as _os
    if _os.environ.get("DECKWEAVER_POW_REQUIRED", "true").lower() not in ("0", "false", "no"):
        ok, reason = pow_challenge.verify(payload.pow_challenge or "", payload.pow_nonce or "")
        if not ok:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"pow:{reason}")
    user = db.query(User).filter(User.username == payload.username).one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        # Constant-time-ish: bcrypt verify already burns the cycles even
        # when user is None (we hash the empty password). Don't disclose
        # which leg failed.
        if not user:
            verify_password(payload.password, "$2b$12$" + "x" * 53)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    token = issue_token(user.username)
    return TokenOut(
        access_token=token,
        username=user.username,
        is_admin=bool(user.is_admin),
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


user_router = APIRouter(prefix="/api/users", tags=["users"])


@user_router.get("", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.id).all()


@user_router.post("", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(409, "Username already exists")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        is_admin=1 if payload.is_admin else 0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@user_router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if user_id == admin.id:
        raise HTTPException(400, "Cannot delete yourself")
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    db.delete(target)
    db.commit()
