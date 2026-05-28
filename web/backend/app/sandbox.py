"""Sandboxing for the conversion subprocess.

Defense in depth, three layers — applied to every `convert.py` spawn:

1. **Environment scrubbing.** Only a whitelist of env vars is forwarded
   (PATH, HOME, locale, model-cache dirs, the GPU toggle). Secrets in
   the web-server's environment (SSH_AUTH_SOCK, AWS_*, OPENAI_*, …)
   never reach the child. See `safe_env()`.

2. **Resource limits.** Applied in a `preexec_fn` just before
   `execve`, so they're inherited by every descendant process spawned
   by `convert.py` (e.g. LibreOffice). Caps virtual memory, CPU time,
   and the size of any single file written. See `make_preexec()`.

3. **Filesystem sandbox.** Wraps the command line so the child can
   *write* only inside its own job directories plus a small allowlist
   of cache dirs (PaddleOCR / EasyOCR / Hugging Face model caches).
   Reads are still wide open so the Python interpreter and ML libs
   keep working. Backend selected automatically:

       macOS  → sandbox-exec (built in)
       Linux  → bwrap (Bubblewrap) if installed
                else firejail if installed
                else no FS sandbox (rlimits + env scrubbing still apply)

   Override with `DECKWEAVER_SANDBOX_MODE = auto | none | sandbox-exec | bwrap | firejail`.

Caveats — be honest about what this does NOT give you:

* This is process-level sandboxing, not a VM. A kernel exploit defeats
  it. For strong isolation, deploy the whole web layer in a container
  / VM as well.
* `sandbox-exec` is technically deprecated by Apple but is still the
  standard userland sandbox tool on macOS and is still functional.
* Network is allowed by default because first-run model downloads
  need it. Set `DECKWEAVER_SANDBOX_ALLOW_NETWORK=false` once caches
  are warm to deny network from inside the sandbox.
"""
from __future__ import annotations

import os
import resource
import shutil
import sys
from pathlib import Path

from .config import REPO_ROOT, get_settings


# Env vars that are safe (and sometimes needed) to forward into the
# conversion subprocess. Anything not in this set is dropped.
_SAFE_ENV_KEYS = {
    "PATH", "HOME", "USER", "LOGNAME",
    "LANG", "LC_ALL", "LC_CTYPE",
    "TMPDIR", "TEMP", "TMP",
    "SHELL",                       # LibreOffice sometimes shells out
    "PYTHONUNBUFFERED", "PYTHONIOENCODING",
    "PYTHONPATH",                  # user-managed; we forward as-is
    "DECKWEAVER_DEVICE",           # GPU / CPU toggle for the CLI
    # VLM credentials + tuning for the cloud-only profile (convert_vlm.py).
    "DECKWEAVER_LLM_BASE", "DECKWEAVER_LLM_KEY",
    "DECKWEAVER_LLM_MODEL", "DECKWEAVER_LLM_FALLBACK",
    "DECKWEAVER_LLM_PARALLEL", "DECKWEAVER_MAX_LONG_EDGE",
    # Model cache locations. If unset, we don't add them; if set by the
    # operator, forward so the subprocess hits the same cache.
    "PADDLE_HOME", "PADDLE_PDX_CACHE_HOME",
    "HF_HOME", "HF_HUB_CACHE", "TRANSFORMERS_CACHE",
    "XDG_CACHE_HOME", "EASYOCR_MODULE_PATH",
    "PADDLEX_HOME", "PADDLEOCR_HOME",
    # Display / GPU stuff some libs probe at import time.
    "DISPLAY", "WAYLAND_DISPLAY",
}


def safe_env() -> dict[str, str]:
    """The minimal env that convert.py needs, with secrets stripped."""
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    # Always set unbuffered so we can stream stdout line-by-line.
    env["PYTHONUNBUFFERED"] = "1"
    return env


def make_preexec(*, memory_mb: int, cpu_seconds: int, output_mb: int):
    """Return a preexec_fn that sets rlimits in the forked child.

    Any zero/negative value disables that particular limit. We never
    raise from preexec — if setrlimit isn't supported (some systems
    don't expose RLIMIT_AS), we silently move on; the other layers
    still apply.
    """
    def _apply() -> None:
        if memory_mb > 0:
            try:
                lim = memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
            except (OSError, ValueError):
                pass
        if cpu_seconds > 0:
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            except (OSError, ValueError):
                pass
        if output_mb > 0:
            try:
                lim = output_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_FSIZE, (lim, lim))
            except (OSError, ValueError):
                pass
        # Detach into a new session so the parent can kill the whole
        # subtree (including any LibreOffice/Paddle workers) with a
        # single killpg() if needed.
        try:
            os.setsid()
        except OSError:
            pass

    return _apply


def _detect_backend(mode: str) -> str:
    """Resolve `auto` to a concrete backend name."""
    mode = (mode or "auto").strip()
    if mode == "none":
        return "none"
    if mode in ("sandbox-exec", "bwrap", "firejail"):
        return mode if shutil.which(mode.split()[0]) else "none"
    # mode == "auto"
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists():
        return "sandbox-exec"
    if shutil.which("bwrap"):
        return "bwrap"
    if shutil.which("firejail"):
        return "firejail"
    return "none"


def _macos_profile(upload_dir: Path, output_dir: Path, allow_network: bool) -> str:
    """sandbox-exec scheme (TinyScheme) profile.

    Starts permissive ("allow default") then *denies* file writes,
    *re-allows* writes only on a small whitelist. Reads stay open so
    we don't have to enumerate every Python / library path on the
    user's system. Network can be off-switched.
    """
    home = Path.home()
    write_subpaths = [
        str(upload_dir),
        str(output_dir),
        # System / user temp.
        "/tmp", "/private/tmp", "/var/folders", "/private/var/folders",
        # Device files — /dev/null, /dev/urandom, /dev/tty, etc. Many
        # Python libraries (dill, multiprocessing, OpenCV …) probe
        # /dev/null at import time; blocking it breaks them. The
        # underlying device permissions still apply.
        "/dev",
        "/private/dev",
        # ML / OCR caches + tool config dirs.
        str(home / ".cache"),
        str(home / ".config"),                       # LibreOffice user profile lives here
        str(home / "Library" / "Caches"),
        str(home / "Library" / "Application Support"),  # macOS LibreOffice
        str(home / ".paddleocr"),
        str(home / ".paddlex"),
        str(home / ".paddle"),
        str(home / ".EasyOCR"),
        str(home / ".huggingface"),
        str(home / ".u2net"),  # rembg sometimes uses this
    ]
    write_lines = "\n".join(
        f'  (subpath "{p}")' for p in write_subpaths
    )
    network_block = "" if allow_network else "(deny network*)\n"
    return f"""(version 1)
(allow default)
{network_block}(deny file-write*)
(allow file-write*
{write_lines}
)
"""


def wrap_command(
    cmd: list[str],
    *,
    upload_dir: Path,
    output_dir: Path,
) -> tuple[list[str], list[Path]]:
    """Wrap `cmd` with the active sandbox backend.

    Returns (new_cmd, cleanup_paths). `cleanup_paths` are temp files
    (e.g. the sandbox-exec profile) that the caller can unlink after
    the subprocess exits.
    """
    s = get_settings()
    backend = _detect_backend(s.sandbox_mode)
    cleanup: list[Path] = []

    if backend == "none":
        return cmd, cleanup

    upload_dir = upload_dir.resolve()
    output_dir = output_dir.resolve()

    if backend == "sandbox-exec":
        profile = _macos_profile(upload_dir, output_dir, s.sandbox_allow_network)
        profile_path = output_dir / ".sandbox.sb"
        profile_path.write_text(profile)
        cleanup.append(profile_path)
        return ["/usr/bin/sandbox-exec", "-f", str(profile_path), *cmd], cleanup

    if backend == "bwrap":
        home = Path.home()
        args: list[str] = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--tmpfs", "/tmp",
            "--proc", "/proc",
            "--dev", "/dev",
            "--bind", str(upload_dir), str(upload_dir),
            "--bind", str(output_dir), str(output_dir),
            # Repo is bound read-write so __pycache__ + any in-repo
            # caches still work (CLI was designed assuming write access).
            "--bind", str(REPO_ROOT), str(REPO_ROOT),
        ]
        for sub in (".cache", ".config", ".paddleocr", ".paddlex", ".paddle",
                    ".EasyOCR", ".huggingface", ".u2net"):
            p = home / sub
            p.mkdir(parents=True, exist_ok=True)
            args += ["--bind", str(p), str(p)]
        args += ["--die-with-parent", "--new-session"]
        args += ["--unshare-net"] if not s.sandbox_allow_network else []
        args += ["--"]
        return args + cmd, cleanup

    if backend == "firejail":
        home = Path.home()
        args = [
            "firejail", "--quiet", "--noprofile", "--private-tmp",
            f"--read-only={home}",
            f"--read-write={upload_dir}",
            f"--read-write={output_dir}",
            f"--read-write={REPO_ROOT}",
        ]
        for sub in (".cache", ".config", ".paddleocr", ".paddlex", ".paddle",
                    ".EasyOCR", ".huggingface", ".u2net"):
            p = home / sub
            p.mkdir(parents=True, exist_ok=True)
            args.append(f"--read-write={p}")
        if not s.sandbox_allow_network:
            args.append("--net=none")
        args.append("--")
        return args + cmd, cleanup

    return cmd, cleanup


def active_backend() -> str:
    """For logs / version endpoint — which backend `auto` resolves to."""
    return _detect_backend(get_settings().sandbox_mode)
