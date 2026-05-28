"""Subprocess wrapper around scripts/convert.py.

Streams stdout, parses progress markers, calls back into the queue.
The subprocess is launched with CWD = repo root so its relative paths
behave identically to a manual CLI invocation, and run through the
sandbox module so it can only write to its own job dirs.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
from collections import deque
from pathlib import Path
from typing import Awaitable, Callable

from .config import REPO_ROOT, get_settings
from . import sandbox

STAGE_RE = re.compile(r"^===\s*(\d+)/(\d+)\s+")
PAGE_RE = re.compile(r"^page\s+(\d+):")
LOG_TAIL_LINES = 200

# job_id -> live asyncio subprocess. Populated by run_convert while the
# child is alive, cleared in the finally block. Used by the cancel
# route to send SIGTERM then SIGKILL.
_running: dict[str, asyncio.subprocess.Process] = {}


def is_running(job_id: str) -> bool:
    return job_id in _running


def request_cancel(job_id: str) -> bool:
    """Try to kill the convert subprocess for `job_id`. Returns True if
    a process was found and signalled. The caller is responsible for
    marking the job 'canceled' in the DB once run_convert returns.

    Safe to call from both sync and async contexts: the call only uses
    os.killpg / proc.terminate, never schedules tasks. The convert
    pipeline always reacts to SIGTERM within a few seconds (any in-flight
    HTTP requests time out at the network layer); the queue worker then
    observes the cancel flag and finalises status='canceled'.
    """
    proc = _running.get(job_id)
    if proc is None or proc.returncode is not None:
        return False
    pid = proc.pid
    try:
        # We start_new_session via setsid() in preexec, so killing the
        # process group also kills any descendants the converter spawns.
        os.killpg(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
            return True
        except Exception:
            return False


def force_kill(job_id: str) -> None:
    """SIGKILL fallback if the SIGTERM grace expires somewhere upstream.
    Currently not auto-invoked — the queue's proc.wait() blocks until
    the child actually exits, so SIGTERM is sufficient in practice.
    Exposed for ops if needed."""
    proc = _running.get(job_id)
    if proc is None or proc.returncode is not None:
        return
    pid = proc.pid
    try:
        os.killpg(pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


async def run_convert(
    *,
    job_id: str | None = None,
    source: Path,
    work_dir: Path,
    upload_dir: Path,
    mode: str,
    on_stage: Callable[[int, int], Awaitable[None]],
    on_page: Callable[[int], Awaitable[None]],
    on_line: Callable[[str], Awaitable[None]],
) -> tuple[int, str]:
    """Run convert.py. Returns (exit_code, full_tail_log)."""
    s = get_settings()
    cmd = [
        s.python_bin,
        str(s.convert_script),
        "--source", str(source),
        "--work-dir", str(work_dir),
        # EasyOCR + Tesseract are optional cross-verifiers; skipping
        # them keeps the prod install (and the sandbox) lean. Set
        # DECKWEAVER_CROSS_VERIFY=true if you've installed them and
        # want belt-and-suspenders OCR confidence.
    ]
    if not s.cross_verify:
        cmd.append("--skip-cross-verify")
    if mode != "full":
        cmd += ["--mode", mode]

    wrapped, cleanup = sandbox.wrap_command(
        cmd, upload_dir=upload_dir, output_dir=work_dir,
    )
    env = sandbox.safe_env()
    preexec = sandbox.make_preexec(
        memory_mb=s.subprocess_memory_mb,
        cpu_seconds=s.subprocess_cpu_seconds,
        output_mb=s.subprocess_output_mb,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *wrapped,
            cwd=str(REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            preexec_fn=preexec,
            start_new_session=False,  # preexec already does setsid
        )
        if job_id:
            _running[job_id] = proc

        tail: deque[str] = deque(maxlen=LOG_TAIL_LINES)
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            tail.append(line)
            await on_line(line)
            m = STAGE_RE.match(line)
            if m:
                await on_stage(int(m.group(1)), int(m.group(2)))
                continue
            m = PAGE_RE.match(line)
            if m:
                await on_page(int(m.group(1)))

        code = await proc.wait()
        return code, "\n".join(tail)
    finally:
        if job_id:
            _running.pop(job_id, None)
        for p in cleanup:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
