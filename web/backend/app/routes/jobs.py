"""Job routes — create, list, get, delete, download, logs."""
from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..deps import get_current_user, get_db
from .. import queue as job_queue
from .. import github_sync
from ..eta import estimate_job
from ..models import Job, User
from ..schemas import BulkDeleteIn, BulkDeleteOut, JobLogOut, JobOut

log = logging.getLogger("deckweaver.jobs")

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
ACCEPTED_EXTS = IMAGE_EXTS | {".pdf", ".zip"}

# Magic-byte prefixes for file-type sanity checking (defense in depth,
# the conversion pipeline ultimately decides what it accepts).
MAGIC_PREFIXES: dict[bytes, set[str]] = {
    b"\x89PNG\r\n\x1a\n": {".png"},
    b"\xff\xd8\xff": {".jpg", ".jpeg"},
    b"RIFF": {".webp"},     # full check below
    b"BM": {".bmp"},
    b"II*\x00": {".tif", ".tiff"},
    b"MM\x00*": {".tif", ".tiff"},
    b"%PDF": {".pdf"},
    b"PK\x03\x04": {".zip"},
    b"PK\x05\x06": {".zip"},  # empty zip
}


def _safe_filename(name: str) -> str:
    """Strip the basename so users can't write outside the job dir."""
    name = os.path.basename(name)
    bad = "/\\\0"
    cleaned = "".join("_" if c in bad else c for c in name).strip()
    cleaned = cleaned.lstrip(".")  # no hidden files / no ".." escape
    return cleaned or "upload"


def _validate_magic(head: bytes, ext: str) -> bool:
    """Best-effort magic-byte check. Returns True if the prefix looks
    consistent with the declared extension."""
    ext = ext.lower()
    if ext not in ACCEPTED_EXTS:
        return False
    for prefix, exts in MAGIC_PREFIXES.items():
        if head.startswith(prefix) and ext in exts:
            if prefix == b"RIFF":
                # RIFF is also used for WAV / AVI; require WEBP marker.
                return head[8:12] == b"WEBP"
            return True
    return False


def _count_pdf_pages(path: Path) -> int:
    # We open the PDF *in the web process* only to count pages for ETA.
    # PyMuPDF parses just enough metadata for page_count; any malformed
    # input here is contained by the surrounding try/except. The full
    # parse happens inside the sandboxed conversion subprocess.
    try:
        import fitz  # PyMuPDF — already a project dep
        with fitz.open(path) as doc:
            return doc.page_count
    except Exception:
        return 5  # conservative fallback so ETA is in the right ballpark


def _safe_unzip(zip_path: Path, dest: Path, *, max_entries: int, max_uncompressed: int) -> list[Path]:
    """Extract a zip safely:

    - reject member names with '..' segments, absolute paths, or drive
      letters → defeats zip-slip / path traversal.
    - reject symlinks → defeats indirect path traversal.
    - cap total entry count and total uncompressed size → defeats zip
      bombs.

    Returns the list of extracted file paths inside `dest`.
    """
    extracted: list[Path] = []
    written = 0
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise HTTPException(400, f"Zip has too many entries (> {max_entries})")
        total_uncompressed = sum(i.file_size for i in infos)
        if total_uncompressed > max_uncompressed:
            raise HTTPException(
                400,
                f"Zip uncompresses to too large: "
                f"{total_uncompressed // (1024*1024)} MB > "
                f"{max_uncompressed // (1024*1024)} MB",
            )
        for info in infos:
            name = info.filename
            # Reject anything that looks like an escape attempt.
            if not name or name.endswith("/"):
                continue  # dir entries handled implicitly
            if "\\" in name or name.startswith("/") or ":" in name:
                raise HTTPException(400, f"Unsafe zip entry: {name!r}")
            parts = Path(name).parts
            if any(p in ("..", "") for p in parts):
                raise HTTPException(400, f"Unsafe zip entry: {name!r}")
            # Symlinks in zip have mode bit S_IFLNK = 0o120000.
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise HTTPException(400, f"Symlinks are not allowed: {name!r}")
            target = (dest / name).resolve()
            try:
                target.relative_to(dest_resolved)
            except ValueError:
                raise HTTPException(400, f"Zip entry escapes target dir: {name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                # Stream-copy with a size guard so an over-stated header
                # can't sneak in extra bytes.
                remaining = max_uncompressed - written
                while True:
                    chunk = src.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    remaining -= len(chunk)
                    if remaining < 0:
                        raise HTTPException(400, "Zip exceeded uncompressed size cap")
                    out.write(chunk)
            extracted.append(target)
    return extracted


def _serialize(db: Session, job: Job, all_jobs: list[Job]) -> JobOut:
    qpos, eta = estimate_job(db, job, all_jobs)
    return JobOut.model_validate({
        **{c.name: getattr(job, c.name) for c in job.__table__.columns},
        "queue_position": qpos,
        "eta_seconds": eta,
    })


@router.post("", response_model=JobOut, status_code=201)
async def create_job(
    mode: Literal["full", "text-only"] = Form("full"),
    name: str | None = Form(None),
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    if github_sync.is_updating():
        raise HTTPException(503, "Service is updating, please retry shortly")

    s = get_settings()
    # Per-user throttling — keep one abusive account from filling the
    # queue or the disk.
    active = (
        db.query(Job)
        .filter(Job.owner_id == user.id)
        .filter(Job.status.in_(("queued", "running")))
        .count()
    )
    if active >= s.per_user_active_jobs:
        raise HTTPException(
            429,
            f"You already have {active} active jobs (cap: {s.per_user_active_jobs})",
        )
    total = db.query(Job).filter(Job.owner_id == user.id).count()
    if total >= s.per_user_total_jobs:
        raise HTTPException(
            409,
            f"You have reached the per-user job cap ({s.per_user_total_jobs}). "
            "Delete some history before submitting more.",
        )

    job_id = str(uuid.uuid4())
    upload_dir = s.uploads_dir / job_id
    output_dir = s.outputs_dir / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if len(files) > s.max_files:
        shutil.rmtree(upload_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)
        raise HTTPException(400, f"Too many files (max {s.max_files})")

    single_cap = s.max_single_file_mb * 1024 * 1024
    total_cap = s.max_upload_mb * 1024 * 1024
    saved_paths: list[Path] = []
    total = 0
    try:
        for upload in files:
            # Note: shadowing the outer `name` (the user-supplied task
            # name) with the per-file safe name was a real bug — keep
            # them clearly separate.
            safe_name = _safe_filename(upload.filename or "upload.bin")
            ext = Path(safe_name).suffix.lower()
            if ext not in ACCEPTED_EXTS:
                raise HTTPException(
                    400,
                    f"Unsupported file type: {ext or '(none)'}",
                )
            dest = upload_dir / safe_name
            size = 0
            with dest.open("wb") as f:
                while True:
                    chunk = upload.file.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    total += len(chunk)
                    if size > single_cap:
                        raise HTTPException(
                            413,
                            f"File '{safe_name}' exceeds {s.max_single_file_mb} MB",
                        )
                    if total > total_cap:
                        raise HTTPException(
                            413,
                            f"Total upload exceeds {s.max_upload_mb} MB",
                        )
                    f.write(chunk)
            # Magic-byte sanity check on the first few bytes.
            with dest.open("rb") as f:
                head = f.read(16)
            if not _validate_magic(head, ext):
                raise HTTPException(
                    400,
                    f"File '{safe_name}' does not match its declared type {ext}",
                )
            saved_paths.append(dest)
    except HTTPException:
        shutil.rmtree(upload_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(upload_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)
        raise

    if not saved_paths:
        shutil.rmtree(upload_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)
        raise HTTPException(400, "No files uploaded")

    # If a single zip was uploaded, extract it in place using a safe
    # extractor (rejects zip-slip / symlinks / zip bombs).
    if len(saved_paths) == 1 and saved_paths[0].suffix.lower() == ".zip":
        zpath = saved_paths[0]
        try:
            _safe_unzip(
                zpath, upload_dir,
                max_entries=s.zip_max_entries,
                max_uncompressed=s.zip_max_uncompressed_mb * 1024 * 1024,
            )
        except HTTPException:
            shutil.rmtree(upload_dir, ignore_errors=True)
            shutil.rmtree(output_dir, ignore_errors=True)
            raise
        finally:
            zpath.unlink(missing_ok=True)
        saved_paths = [p for p in upload_dir.rglob("*")
                       if p.is_file() and not p.is_symlink()]

    # Classify the source (only images / pdf get past _safe_unzip too).
    images = [p for p in saved_paths if p.suffix.lower() in IMAGE_EXTS]
    pdfs = [p for p in saved_paths if p.suffix.lower() == ".pdf"]

    if len(pdfs) == 1 and not images:
        source_kind = "pdf"
        source_filename = pdfs[0].name
        page_count = _count_pdf_pages(pdfs[0])
    elif len(images) == 1 and not pdfs:
        source_kind = "image"
        source_filename = images[0].name
        page_count = 1
    elif images and not pdfs:
        source_kind = "dir"
        source_filename = f"{len(images)} images"
        page_count = len(images)
    else:
        shutil.rmtree(upload_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)
        raise HTTPException(400, "Unsupported upload — provide images, one PDF, or a zip of images")

    # Use the client-provided name if non-empty, else fall back to the
    # derived file description. Either way it's just a display label —
    # the actual files on disk are immutable inside upload_dir.
    display_name = (name or "").strip() or source_filename
    if len(display_name) > 256:
        display_name = display_name[:256]

    job = Job(
        id=job_id,
        owner_id=user.id,
        source_filename=display_name,
        source_kind=source_kind,
        mode=mode,
        status="queued",
        page_count=page_count,
        upload_dir=str(upload_dir),
        output_dir=str(output_dir),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    await job_queue.enqueue(job_id)
    all_jobs = db.query(Job).all()
    return _serialize(db, job, all_jobs)


@router.get("", response_model=list[JobOut])
def list_jobs(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(Job).order_by(Job.created_at.desc())
    if not user.is_admin:
        q = q.filter(Job.owner_id == user.id)
    jobs = q.all()
    all_jobs = db.query(Job).all()
    return [_serialize(db, j, all_jobs) for j in jobs]


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not user.is_admin and job.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    return _serialize(db, job, db.query(Job).all())


@router.get("/{job_id}/logs", response_model=JobLogOut)
def get_job_logs(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not user.is_admin and job.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    return JobLogOut(id=job.id, log_tail=job.log_tail or "")


@router.get("/{job_id}/download")
def download_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not user.is_admin and job.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    if job.status != "done":
        raise HTTPException(409, f"Job is {job.status}, not done")
    pptx = Path(job.output_dir) / "slides.pptx"
    if not pptx.exists():
        raise HTTPException(404, "slides.pptx missing")
    base = Path(job.source_filename).stem or "deck"
    return FileResponse(
        path=pptx,
        filename=f"{base}.pptx",
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


def _terminate_and_wait(job_id: str, *, grace_seconds: float = 5.0) -> None:
    """Best-effort SIGTERM + brief wait for the worker to flip status.
    Called by force-delete so the convert subprocess releases its file
    handles before we rmtree the dirs.
    """
    from ..runner import request_cancel, is_running
    job_queue.mark_cancelled(job_id)
    request_cancel(job_id)
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not is_running(job_id):
            return
        time.sleep(0.1)
    log.warning("force-delete: convert subprocess for %s did not exit in %ss",
                job_id, grace_seconds)


def _delete_one(db: Session, job: Job, *, force: bool) -> tuple[bool, str | None]:
    """Try to delete a single job. Returns (deleted_ok, skip_reason).

    Only deletes the DB row when the disk cleanup succeeded. If files
    can't be removed (busy / perm), keep the row so the next sweep can
    finish the job — never leave the row gone with files behind.
    """
    if job.status == "running":
        if not force:
            return False, "running (use force=true to cancel+delete)"
        _terminate_and_wait(job.id)
        db.refresh(job)
    ok = job_queue.cleanup_job_dirs(job)
    if not ok:
        # Disk cleanup partially failed — surface a clear error so the
        # client can retry. Keep the DB row so the orphan sweeper can
        # finish the cleanup on its next pass.
        return False, "disk cleanup failed; row kept for sweeper to retry"
    db.delete(job)
    return True, None


@router.delete("/{job_id}", status_code=204)
def delete_job(
    job_id: str,
    force: bool = Query(False, description="If true, cancel running jobs before deleting"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not user.is_admin and job.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    deleted, reason = _delete_one(db, job, force=force)
    if not deleted:
        # 409 keeps "running without force" semantics that clients
        # already expect; 500 covers the disk-cleanup-failed case.
        code = 409 if reason and "running" in reason else 500
        raise HTTPException(code, reason or "delete failed")
    db.commit()


@router.post("/bulk-delete", response_model=BulkDeleteOut)
def bulk_delete_jobs(
    payload: BulkDeleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> BulkDeleteOut:
    """Delete multiple jobs in one round-trip, with per-id success/skip
    reporting. Each id is processed independently — one failure does
    not abort the others — and only the rows whose disk cleanup
    succeeds are removed from the DB. This is what the frontend's
    'clear history' action calls so the server can do file cleanup
    transactionally per-id instead of relying on N parallel HTTP DELETEs."""
    deleted: list[str] = []
    skipped: list[dict] = []
    freed_bytes = 0

    for jid in payload.ids:
        job = db.get(Job, jid)
        if not job:
            skipped.append({"id": jid, "reason": "not found"})
            continue
        if not user.is_admin and job.owner_id != user.id:
            skipped.append({"id": jid, "reason": "forbidden"})
            continue
        # Estimate freed bytes before we rmtree.
        size = 0
        for p in (Path(job.upload_dir), Path(job.output_dir)):
            if p.exists():
                for f in p.rglob("*"):
                    try:
                        if f.is_file():
                            size += f.stat().st_size
                    except OSError:
                        pass
        ok, reason = _delete_one(db, job, force=payload.force)
        if ok:
            deleted.append(jid)
            freed_bytes += size
        else:
            skipped.append({"id": jid, "reason": reason or "delete failed"})

    if deleted:
        db.commit()

    return BulkDeleteOut(
        deleted=deleted,
        skipped=skipped,
        storage_freed_mb=freed_bytes // (1024 * 1024),
    )


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-run a failed or canceled job using its original upload_dir.

    Resets progress fields, clears the output_dir (the prior run may
    have written a half-finished slides.pptx), keeps the same row id so
    history and URLs stay stable, and re-enqueues. We do NOT allow
    retrying done jobs — that's a fresh upload, not a retry.
    """
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not user.is_admin and job.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    if job.status not in ("failed", "canceled"):
        raise HTTPException(
            409,
            f"Only failed or canceled jobs can be retried (current: {job.status})",
        )

    upload_dir = Path(job.upload_dir)
    if not upload_dir.is_dir() or not any(upload_dir.iterdir()):
        # The original source files were swept away (retention / orphan
        # cleanup / manual rm). Without them there's nothing to convert.
        raise HTTPException(
            410,
            "Original upload files are gone — re-upload the source to start a new job",
        )

    s = get_settings()
    if github_sync.is_updating():
        raise HTTPException(503, "Service is updating, please retry shortly")
    # Throttle: count this job as a new active job, so retry can't bypass the cap.
    active = (
        db.query(Job)
        .filter(Job.owner_id == user.id)
        .filter(Job.status.in_(("queued", "running")))
        .count()
    )
    if active >= s.per_user_active_jobs:
        raise HTTPException(
            429,
            f"You already have {active} active jobs (cap: {s.per_user_active_jobs})",
        )

    # Wipe the prior output. Failed runs may have left a half-written
    # pptx that download_job would otherwise still happily serve.
    output_dir = Path(job.output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=False)
    output_dir.mkdir(parents=True, exist_ok=True)

    job.status = "queued"
    job.progress_pct = 0
    job.current_page = 0
    job.error_msg = None
    job.log_tail = None
    job.started_at = None
    job.finished_at = None
    job.duration_seconds = 0
    db.commit()
    db.refresh(job)

    await job_queue.enqueue(job_id)
    return _serialize(db, job, db.query(Job).all())


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Cancel a queued or running job. Queued jobs flip to 'canceled'
    immediately; running jobs receive SIGTERM (then SIGKILL after a
    grace period) and the queue picks up the cancellation flag on
    teardown."""
    from ..runner import request_cancel

    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not user.is_admin and job.owner_id != user.id:
        raise HTTPException(403, "Forbidden")
    if job.status in ("done", "failed", "canceled"):
        raise HTTPException(409, f"Job already in terminal state: {job.status}")

    job_queue.mark_cancelled(job_id)

    if job.status == "running":
        signalled = request_cancel(job_id)
        if not signalled:
            # Subprocess already exited; the queue will sort it out.
            pass
    else:  # queued — never started; flip the status here so it doesn't run
        job.status = "canceled"
        job.error_msg = "cancelled before start"
        from datetime import datetime, timezone as _tz
        job.finished_at = datetime.now(_tz.utc)
        db.commit()
        db.refresh(job)

    all_jobs = db.query(Job).all()
    return _serialize(db, job, all_jobs)
