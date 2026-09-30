from datetime import datetime, timezone
from threading import Thread
from typing import Optional
from uuid import uuid4

from ..db import database, one
from .clip_service import generate_clip, generate_montage
from .highlight_service import create_highlights, get_highlight
from .transcription_service import installed as whisper_installed, transcribe
from .video_service import get_video


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobConflictError(RuntimeError):
    """Raised when an equivalent job is already running for the same video."""


def active_job(video_id: str, job_type: str) -> Optional[dict]:
    with database() as db:
        return one(
            db.execute(
                """SELECT * FROM jobs WHERE video_id=? AND type=? AND status IN ('QUEUED','RUNNING')
                   ORDER BY created_at DESC LIMIT 1""",
                (video_id, job_type),
            ).fetchone()
        )


def create_job(video_id: str, job_type: str, highlight_id: Optional[str] = None, subtitles: bool = True, style: str = "crop") -> dict:
    if job_type == "ANALYZE" and active_job(video_id, job_type):
        raise JobConflictError("Анализ этого видео уже выполняется. Дождитесь завершения.")
    if style not in ("crop", "blur"):
        raise ValueError("Неизвестный стиль экспорта.")
    job = {
        "id": uuid4().hex,
        "video_id": video_id,
        "type": job_type,
        "status": "QUEUED",
        "progress": 0,
        "current_step": "QUEUED",
        "error": None,
        "cancelled": 0,
        "created_at": _now(),
        "updated_at": _now(),
        "subtitles": 1 if subtitles else 0,
        "style": style,
    }
    with database() as db:
        db.execute(
            """INSERT INTO jobs VALUES (:id,:video_id,:type,:status,:progress,:current_step,:error,:cancelled,:created_at,:updated_at,:subtitles,:style)""",
            job,
        )
    Thread(target=_run, args=(job["id"], highlight_id), daemon=True).start()
    return job


def get_job(job_id: str) -> Optional[dict]:
    with database() as db:
        return one(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def cancel_job(job_id: str) -> Optional[dict]:
    with database() as db:
        db.execute(
            """UPDATE jobs SET cancelled=1, status='FAILED', current_step='CANCELLED',
               error=COALESCE(NULLIF(error,''), 'Обработка отменена пользователем.'),
               updated_at=? WHERE id=? AND status IN ('QUEUED','RUNNING')""",
            (_now(), job_id),
        )
    return get_job(job_id)


def _update(job_id: str, **values) -> None:
    values["updated_at"] = _now()
    clause = ", ".join(f"{key}=?" for key in values)
    with database() as db:
        db.execute(f"UPDATE jobs SET {clause} WHERE id=?", (*values.values(), job_id))


def _cancelled(job_id: str) -> bool:
    job = get_job(job_id)
    return bool(job and job["cancelled"])


def _run(job_id: str, highlight_id: Optional[str]) -> None:
    try:
        job = get_job(job_id)
        if not job:
            return
        _update(job_id, status="RUNNING", progress=10, current_step="PREPARING")
        if _cancelled(job_id):
            _update(job_id, status="FAILED", current_step="CANCELLED", error="Обработка отменена пользователем.")
            return
        video = get_video(job["video_id"])
        if not video:
            raise ValueError("Видео не найдено.")
        if job["type"] == "ANALYZE":
            segments = None
            if whisper_installed():
                _update(job_id, progress=20, current_step="TRANSCRIBING")

                def on_progress(step: str, progress: int) -> None:
                    if not _cancelled(job_id):
                        _update(job_id, progress=progress, current_step=step)

                try:
                    segments = transcribe(video, on_progress=on_progress, cancel_check=lambda: _cancelled(job_id))
                except RuntimeError as error:
                    if "отменена" in str(error).lower():
                        _update(job_id, status="FAILED", current_step="CANCELLED", error=str(error))
                        return
                    # Whisper optional: network/model issues fall back to mock analysis.
                    segments = None
                if _cancelled(job_id):
                    _update(job_id, status="FAILED", current_step="CANCELLED", error="Обработка отменена пользователем.")
                    return
            _update(job_id, progress=70, current_step="ANALYZING")
            create_highlights(video, segments)
            _update(job_id, status="DONE", progress=100, current_step="DONE")
        elif job["type"] == "RENDER":
            _update(job_id, progress=35, current_step="RENDERING")
            highlight = get_highlight(highlight_id or "")
            if not highlight:
                raise ValueError("Момент не найден.")
            if _cancelled(job_id):
                _update(job_id, status="FAILED", current_step="CANCELLED", error="Обработка отменена пользователем.")
                return
            generate_clip(video, highlight, with_subtitles=bool(job.get("subtitles", 1)), style=str(job.get("style", "crop")))
            _update(job_id, status="DONE", progress=100, current_step="DONE")
        elif job["type"] == "MONTAGE":
            _update(job_id, progress=35, current_step="RENDERING")
            highlight = get_highlight(highlight_id or "")
            if not highlight:
                raise ValueError("Момент не найден.")
            if _cancelled(job_id):
                _update(job_id, status="FAILED", current_step="CANCELLED", error="Обработка отменена пользователем.")
                return
            generate_montage(video, highlight, with_subtitles=bool(job.get("subtitles", 1)), style=str(job.get("style", "crop")))
            _update(job_id, status="DONE", progress=100, current_step="DONE")
        else:
            raise ValueError("Неизвестный тип задачи.")
    except Exception as error:
        _update(job_id, status="FAILED", current_step="FAILED", error=str(error))
