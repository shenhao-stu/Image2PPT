"""FastAPI app entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import WEB_ROOT, get_settings
from .db import SessionLocal, init_db
from .middleware import MaxBodySizeMiddleware, SecurityHeadersMiddleware
from .models import User
from .retention import sweeper_loop
from .security import hash_password
from . import queue as job_queue
from . import github_sync
from .routes.auth import router as auth_router, user_router
from .routes.jobs import router as jobs_router
from .routes.system import router as system_router, ws_router

log = logging.getLogger("deckweaver.web")


_PLACEHOLDER_PASSWORDS = {"admin", "password", "changeme", ""}
_PLACEHOLDER_SECRET_FRAGMENTS = (
    "change-me", "please-set", "dev-only", "placeholder",
)


def _check_secrets() -> None:
    """Refuse to boot when DECKWEAVER_REQUIRE_SECURE_SECRETS is on and
    the JWT secret or admin password are still placeholder values."""
    s = get_settings()
    if not s.require_secure_secrets:
        return
    problems: list[str] = []
    if s.admin_password.lower() in _PLACEHOLDER_PASSWORDS:
        problems.append(
            "DECKWEAVER_ADMIN_PASSWORD is unset or a known weak default"
        )
    secret = s.jwt_secret.lower()
    if len(secret) < 32 or any(frag in secret for frag in _PLACEHOLDER_SECRET_FRAGMENTS):
        problems.append(
            "DECKWEAVER_JWT_SECRET is too short or still the placeholder"
        )
    if problems:
        bullets = "\n  - ".join(problems)
        raise RuntimeError(
            "Refusing to start with insecure defaults:\n  - "
            + bullets
            + "\n\nFix web/.env, or set DECKWEAVER_REQUIRE_SECURE_SECRETS=false "
            "for trusted single-machine deploys."
        )


def _ensure_admin() -> None:
    s = get_settings()
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == s.admin_username).one_or_none()
        if existing:
            return
        if db.query(User).filter(User.is_admin == 1).count() > 0:
            return
        admin = User(
            username=s.admin_username,
            password_hash=hash_password(s.admin_password),
            is_admin=1,
        )
        db.add(admin)
        db.commit()
        log.info("Seeded admin user '%s' from environment", s.admin_username)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_secrets()
    init_db()
    _ensure_admin()
    await job_queue.boot_recover()
    worker_task = asyncio.create_task(job_queue.worker_loop())
    sync_task = asyncio.create_task(github_sync.poll_loop())
    sweep_task = asyncio.create_task(sweeper_loop())
    try:
        yield
    finally:
        for t in (worker_task, sync_task, sweep_task):
            t.cancel()
        for t in (worker_task, sync_task, sweep_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="DeckWeaver Web", version="0.1.0", lifespan=lifespan)
    # 1 MiB slack on top of the configured upload cap (multipart framing).
    body_cap = s.max_upload_mb * 1024 * 1024 + 1024 * 1024
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=body_cap)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(auth_router)
    app.include_router(user_router)
    app.include_router(jobs_router)
    app.include_router(system_router)
    app.include_router(ws_router)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # If a built frontend exists (web/frontend/dist), serve it. Otherwise
    # rely on the Vite dev server hitting us through its proxy.
    dist = WEB_ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        # Top-level static files Vite copies from /public/ at build time
        # (e.g. favicon.png, robots.txt). Each one needs an explicit
        # route — the SPA catchall below would otherwise mask them with
        # index.html.
        for _name in ("favicon.png", "favicon.ico", "robots.txt"):
            _file = dist / _name
            if _file.is_file():
                def _make_handler(p):
                    return lambda: FileResponse(p)
                app.add_api_route(f"/{_name}", _make_handler(_file),
                                  methods=["GET"], include_in_schema=False)

        @app.get("/")
        @app.get("/{path:path}")
        def spa_index(path: str = ""):
            # API/WS routes are matched earlier by FastAPI's router order.
            # For an unknown path under /api or /ws, return a real 404
            # instead of the SPA shell to keep clients honest.
            if path.startswith("api/") or path.startswith("ws/"):
                from fastapi import HTTPException
                raise HTTPException(404, "Not found")
            return FileResponse(dist / "index.html")

    return app


app = create_app()
