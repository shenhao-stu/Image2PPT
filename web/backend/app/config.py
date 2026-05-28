"""Runtime configuration loaded from environment.

The web layer is deliberately additive: nothing here mutates the
existing project. All runtime state (DB, uploads, outputs) lives under
web/data/ which is gitignored.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_ROOT = REPO_ROOT / "web"
DATA_ROOT = WEB_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DECKWEAVER_",
        env_file=str(WEB_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Auth
    admin_username: str = "admin"
    admin_password: str = "admin"
    jwt_secret: str = "change-me-please-set-DECKWEAVER_JWT_SECRET"
    jwt_alg: str = "HS256"
    jwt_ttl_hours: int = 24 * 7

    # Storage
    db_path: Path = DATA_ROOT / "deckweaver.db"
    uploads_dir: Path = DATA_ROOT / "uploads"
    outputs_dir: Path = DATA_ROOT / "outputs"

    # Worker
    python_bin: str = "python3"
    convert_script: Path = REPO_ROOT / "scripts" / "convert.py"
    # Run EasyOCR + Tesseract cross-verification against PaddleOCR.
    # Defaults to off because EasyOCR pulls in torch (~1 GB of CUDA
    # wheels). Set to true only when both are installed.
    cross_verify: bool = False

    # GitHub auto-update — OFF by default. Enabling pulls remote code
    # and re-execs the server, so a compromised upstream becomes RCE.
    # Trusted single-machine deploys can opt in via env.
    auto_update: bool = False
    update_poll_seconds: int = 600
    git_remote: str = "origin"
    git_branch: str = "main"
    github_repo_url: str = "https://github.com/shenhao-stu/Image2PPT"

    # CORS (frontend dev server)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Sandboxing and resource limits for the conversion subprocess.
    # sandbox_mode: auto (best available) | none | sandbox-exec | bwrap | firejail
    sandbox_mode: str = "auto"
    # Allow network inside the sandbox. First-run model downloads need it;
    # once caches are warm you can set this to false for a tighter posture.
    sandbox_allow_network: bool = True
    # Conversion subprocess caps. 0 = disabled.
    subprocess_memory_mb: int = 6144      # 6 GB virtual memory
    subprocess_cpu_seconds: int = 3600    # 60 min CPU
    subprocess_output_mb: int = 512       # max single file written by child

    # Upload caps.
    max_upload_mb: int = 200              # total bytes across all uploaded files
    max_files: int = 100                  # max files per submission
    max_single_file_mb: int = 100         # max bytes per individual file
    # Zip-bomb defenses.
    zip_max_entries: int = 500
    zip_max_uncompressed_mb: int = 1024

    # Per-user throttling — keep one abusive user from filling the queue
    # or the disk. Counts include queued + running.
    per_user_active_jobs: int = 2
    per_user_total_jobs: int = 50

    # Auth abuse protection.
    login_rate_per_min: int = 10        # per source IP. 0 disables.

    # Retention sweeper for finished jobs (done/failed/canceled).
    job_retention_days: int = 30        # 0 disables.
    retention_sweep_seconds: int = 3600

    # Refuse to boot in production-looking environments if these are
    # still at their development defaults. Public deploys MUST set both.
    require_secure_secrets: bool = True

    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
