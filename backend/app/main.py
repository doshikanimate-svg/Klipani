import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .db import DB_FILENAME, initialize
from .providers.llm.factory import active_provider_name
from .providers.llm.ollama import ollama_available
from .providers.publishing.base import PublishMetadata
from .providers.publishing.youtube import YouTubeProvider
from .providers.publishing.tiktok import TikTokProvider
from .services.analysis_service import get_analysis
from .services.app_settings import get_all as get_app_settings
from .services.app_settings import update as update_app_settings
from .services.clip_service import delete_clip, get_clip, list_clips
from .services.highlight_service import get_highlight, list_highlights, update_highlight
from .services.job_service import JobConflictError, cancel_job, create_job, get_job
from .services.publication_service import list_publications, save_publication
from .services.stats_service import get_clip_stats, put_clip_stats
from .services.transcription_service import get_transcript, installed as whisper_installed, model_cached as whisper_model_ready
from .services.video_service import create_video, get_video, list_videos
from .utils.ffmpeg import VideoToolError, available
from .utils.filesystem import safe_upload_name

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize()
    from .services.dapayments import start_poller

    start_poller()
    # Webhook setup does network I/O (Telegram API) — must not block startup,
    # or the hoster kills the process on port-scan timeout.
    webhook_task = asyncio.create_task(_start_telegram_webhook())
    try:
        yield
    finally:
        webhook_task.cancel()
        # NOTE: do NOT delete_webhook() here. If the next process fails to
        # re-register (missing env, crash), Telegram would have nowhere to
        # deliver updates and the bot goes silently dead. Re-setting the same
        # webhook on startup is idempotent.
        bot = getattr(app.state, "bot", None)
        if bot is not None:
            try:
                await bot.session.close()
            except Exception:
                pass


async def _start_telegram_webhook():
    """On hosting (PUBLIC_URL set): Telegram delivers updates via webhook.

    Locally (no PUBLIC_URL): nothing starts here, use bot/main.py polling.
    """
    import asyncio as _asyncio

    settings = get_settings()
    if not settings.telegram_bot_token:
        print("telegram webhook skipped: TELEGRAM_BOT_TOKEN is empty")
        return None
    if not settings.public_url:
        print("telegram webhook skipped: PUBLIC_URL is empty")
        return None
    try:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bot"))
        from main import create_bot  # noqa: E402

        bot, dispatcher = create_bot()
        url = settings.public_url.rstrip("/") + "/api/bot/webhook"
        try:
            await _asyncio.wait_for(
                bot.set_webhook(url, secret_token=settings.telegram_webhook_secret or None),
                timeout=20,
            )
        except _asyncio.TimeoutError:
            print("telegram webhook set timed out, will retry on next restart")
            await bot.session.close()
            return None
        app.state.bot = bot
        app.state.dispatcher = dispatcher
        print(f"telegram webhook set: {url}")
        return bot
    except Exception as error:
        print(f"telegram webhook not started: {error}")
        return None


app = FastAPI(title="KLIPANI", version="0.3.0", lifespan=lifespan)


@app.middleware("http")
async def license_gate(request: Request, call_next):
    """Paid work requires a valid key. Read-only endpoints stay open so the user
    can still see their clips and buy a subscription."""
    if not settings.license_enforced:
        return await call_next(request)
    method, path = request.method, request.url.path
    # Bot/payment endpoints run server-side on the host and have no local key.
    public = path.startswith(("/api/health", "/api/license", "/api/bot", "/api/payments"))
    # Cancelling must always work, even if the key expires mid-render.
    if public or method not in ("POST", "PUT", "PATCH", "DELETE") or path.endswith("/cancel"):
        return await call_next(request)
    from .services.license_service import current_state

    state = current_state()
    if state["active"]:
        return await call_next(request)
    detail = (
        f"Подписка истекла {time.strftime('%d.%m.%Y', time.localtime(state['exp']))}."
        if state.get("exp")
        else "Нет активной подписки. Пробная доступна 3 дня."
    )
    return JSONResponse(
        status_code=402,
        content={"detail": f"{detail} Продлить: @Klipani_bot", "code": "license_required", **state},
    )
app.add_middleware(
    CORSMiddleware,
    # LAN origins for the mobile companion (same Wi-Fi); no cookies used.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


class HighlightBounds(BaseModel):
    start_time: float = Field(..., ge=0)
    end_time: float = Field(..., gt=0)


class PublishRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    privacy: str = Field(default="unlisted", pattern="^(private|unlisted|public)$")


class ClipStatsBody(BaseModel):
    views: int = Field(default=0, ge=0)
    likes: int = Field(default=0, ge=0)


class LicenseBody(BaseModel):
    key: str = Field(..., min_length=10, max_length=500)


@app.get("/api/license")
def license_status() -> dict:
    from .services.license_service import current_state

    return current_state()


@app.post("/api/license")
def license_activate(body: LicenseBody) -> dict:
    from .services.app_settings import set_private
    from .services.license_service import verify_license

    key = body.key.strip()
    try:
        info = verify_license(key)
    except RuntimeError as error:
        raise HTTPException(400, str(error)) from error
    if not info:
        raise HTTPException(400, "Ключ недействителен или истёк.")
    set_private("license_key", key)
    return {"active": True, **info}


@app.get("/api/settings")
def read_settings() -> dict:
    return get_app_settings()


@app.put("/api/settings")
def write_settings(body: dict) -> dict:
    try:
        return update_app_settings(body or {})
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


def required_video(video_id: str) -> dict:
    video = get_video(video_id)
    if not video:
        raise HTTPException(404, "Видео не найдено.")
    return video


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "ffmpeg": available("ffmpeg"),
        "ffprobe": available("ffprobe"),
        "whisper": whisper_installed(),
        "whisper_ready": whisper_model_ready(),
        "llm": ollama_available(),
        "llm_provider": active_provider_name(),
        "whisper_model": settings.whisper_model,
    }


@app.post("/api/videos/upload")
async def upload_video(file: UploadFile = File(...)) -> dict:
    try:
        safe_name = safe_upload_name(file.filename or "")
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    destination = settings.storage / "uploads" / safe_name
    size = 0
    try:
        with destination.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                output.write(chunk)
        return create_video(file.filename or safe_name, destination, size)
    except VideoToolError as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(422, str(error)) from error
    finally:
        await file.close()


@app.get("/api/videos")
def videos() -> list[dict]:
    return list_videos()


@app.get("/api/storage")
def storage_usage() -> dict:
    total = 0
    breakdown: dict[str, int] = {}
    for name in ("uploads", "clips", "thumbnails", "transcripts", "working"):
        directory = settings.storage / name
        size = sum(p.stat().st_size for p in directory.rglob("*") if p.is_file()) if directory.exists() else 0
        breakdown[name] = size
        total += size
    db_path = settings.storage / DB_FILENAME
    db_size = db_path.stat().st_size if db_path.exists() else 0
    return {"total": total + db_size, "database": db_size, **breakdown}


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: str) -> dict:
    from .services.video_service import delete_video as remove_video

    if not get_video(video_id):
        raise HTTPException(404, "Видео не найдено.")
    return remove_video(video_id)


@app.delete("/api/storage/cache")
def clear_cache(keep_video_id: Optional[str] = None) -> dict:
    from .services.video_service import clear_cache as sweep_cache

    if keep_video_id and not get_video(keep_video_id):
        raise HTTPException(404, "Видео не найдено.")
    return sweep_cache(keep_video_id)


@app.get("/api/videos/{video_id}")
def video(video_id: str) -> dict:
    return required_video(video_id)


@app.get("/api/videos/{video_id}/transcript")
def transcript(video_id: str) -> dict:
    required_video(video_id)
    result = get_transcript(video_id)
    if not result:
        raise HTTPException(404, "Транскрипт ещё не создан. Запустите анализ с установленным Whisper.")
    return result


@app.post("/api/videos/{video_id}/analyze")
def analyze(video_id: str) -> dict:
    required_video(video_id)
    try:
        return _job_payload(create_job(video_id, "ANALYZE"))
    except JobConflictError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/videos/{video_id}/highlights")
def highlights(video_id: str) -> list[dict]:
    required_video(video_id)
    return list_highlights(video_id)


@app.get("/api/videos/{video_id}/analysis")
def analysis(video_id: str) -> dict:
    required_video(video_id)
    result = get_analysis(video_id)
    if not result:
        raise HTTPException(404, "Анализ ещё не запускался.")
    return result


@app.patch("/api/highlights/{highlight_id}")
def patch_highlight(highlight_id: str, body: HighlightBounds) -> dict:
    highlight = get_highlight(highlight_id)
    if not highlight:
        raise HTTPException(404, "Момент не найден.")
    video = required_video(highlight["video_id"])
    try:
        updated = update_highlight(highlight_id, body.start_time, body.end_time, float(video["duration"]))
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not updated:
        raise HTTPException(404, "Момент не найден.")
    return updated


@app.get("/api/clips/{clip_id}/stats")
def clip_stats(clip_id: str) -> dict:
    if not get_clip(clip_id):
        raise HTTPException(404, "Клип не найден.")
    return get_clip_stats(clip_id)


@app.put("/api/clips/{clip_id}/stats")
def save_clip_stats(clip_id: str, body: ClipStatsBody) -> dict:
    if not get_clip(clip_id):
        raise HTTPException(404, "Клип не найден.")
    return put_clip_stats(clip_id, body.views, body.likes)


def _job_payload(job: dict) -> dict:
    """Job dict plus the ETA the UI shows as «≈ 4 мин»."""
    from .services.estimate_service import estimate_remaining

    return {**job, "eta_seconds": estimate_remaining(job, get_video(job["video_id"]))}


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict:
    result = get_job(job_id)
    if not result:
        raise HTTPException(404, "Задача не найдена.")
    return _job_payload(result)


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str) -> dict:
    result = cancel_job(job_id)
    if not result:
        raise HTTPException(404, "Задача не найдена.")
    return result


@app.post("/api/highlights/{highlight_id}/generate")
def generate(highlight_id: str, subtitles: bool = True, style: str = "crop") -> dict:
    highlight = get_highlight(highlight_id)
    if not highlight:
        raise HTTPException(404, "Момент не найден.")
    try:
        return _job_payload(create_job(highlight["video_id"], "RENDER", highlight_id, subtitles=subtitles, style=style))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/highlights/{highlight_id}/montage")
def montage(highlight_id: str, subtitles: bool = True, style: str = "crop") -> dict:
    highlight = get_highlight(highlight_id)
    if not highlight:
        raise HTTPException(404, "Момент не найден.")
    try:
        return _job_payload(create_job(highlight["video_id"], "MONTAGE", highlight_id, subtitles=subtitles, style=style))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/videos/{video_id}/montage")
def montage_video(video_id: str, subtitles: bool = True, style: str = "crop") -> dict:
    required_video(video_id)
    highlights = list_highlights(video_id)
    if not highlights:
        raise HTTPException(404, "Нет моментов для монтажа. Сначала запустите анализ.")
    top = max(highlights, key=lambda h: (h["score"], -h["start_time"]))
    try:
        return _job_payload(create_job(video_id, "MONTAGE", top["id"], subtitles=subtitles, style=style))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/clips")
def clips(video_id: Optional[str] = None) -> list[dict]:
    return list_clips(video_id)


@app.get("/api/clips/{clip_id}")
def clip(clip_id: str) -> dict:
    result = get_clip(clip_id)
    if not result:
        raise HTTPException(404, "Клип не найден.")
    return result


@app.get("/api/clips/{clip_id}/video")
def clip_video(clip_id: str):
    result = get_clip(clip_id)
    if not result or not Path(result["output_path"]).exists():
        raise HTTPException(404, "Файл клипа не найден.")
    return FileResponse(result["output_path"], media_type="video/mp4", filename=f"clip-{clip_id}.mp4")


@app.delete("/api/clips/{clip_id}")
def remove_clip(clip_id: str) -> dict:
    if not get_clip(clip_id):
        raise HTTPException(404, "Клип не найден.")
    return delete_clip(clip_id)


@app.get("/api/clips/{clip_id}/thumbnail")
def clip_thumbnail(clip_id: str):
    result = get_clip(clip_id)
    if not result or not Path(result["thumbnail_path"]).exists():
        raise HTTPException(404, "Превью не найдено.")
    return FileResponse(result["thumbnail_path"], media_type="image/jpeg")


def _youtube() -> YouTubeProvider:
    return YouTubeProvider()


@app.get("/api/publish/youtube/status")
def youtube_status() -> dict:
    provider = _youtube()
    return {
        "provider": "youtube",
        "configured": provider.is_configured(),
        "connected": provider.is_connected(),
        "channel": provider.channel_title() if provider.is_connected() else None,
    }


@app.get("/api/publish/youtube/auth-url")
def youtube_auth_url() -> dict:
    try:
        return {"url": _youtube().auth_url()}
    except RuntimeError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/publish/youtube/callback", response_class=HTMLResponse)
def youtube_callback(code: Optional[str] = None) -> str:
    if not code:
        raise HTTPException(400, "OAuth-код не получен.")
    try:
        _youtube().exchange_code(code)
    except Exception as error:
        raise HTTPException(400, f"Не удалось завершить авторизацию: {error}") from error
    return "<html><body><h2>YouTube подключён. Вернитесь в KLIPANI и обновите страницу.</h2></body></html>"


@app.get("/api/clips/{clip_id}/publications")
def clip_publications(clip_id: str) -> list[dict]:
    if not get_clip(clip_id):
        raise HTTPException(404, "Клип не найден.")
    return list_publications(clip_id)


@app.post("/api/clips/{clip_id}/publish")
def publish_clip(clip_id: str, body: PublishRequest) -> dict:
    clip = get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Клип не найден.")
    provider = _youtube()
    if not provider.is_connected():
        raise HTTPException(409, "YouTube не подключён. Пройдите OAuth-авторизацию.")
    try:
        result = provider.publish(
            clip["output_path"],
            PublishMetadata(title=body.title, description=body.description, privacy=body.privacy),
        )
    except RuntimeError as error:
        raise HTTPException(502, str(error)) from error
    return save_publication(clip_id, result.provider, result.external_id, result.url)


def _tiktok() -> TikTokProvider:
    return TikTokProvider()


@app.get("/api/publish/tiktok/status")
def tiktok_status() -> dict:
    provider = _tiktok()
    return {
        "provider": "tiktok",
        "configured": provider.is_configured(),
        "connected": provider.is_connected(),
        "channel": None,
    }


@app.get("/api/publish/tiktok/auth-url")
def tiktok_auth_url() -> dict:
    try:
        return {"url": _tiktok().auth_url()}
    except RuntimeError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/publish/tiktok/callback", response_class=HTMLResponse)
def tiktok_callback(code: Optional[str] = None, state: Optional[str] = None) -> str:
    if not code:
        raise HTTPException(400, "OAuth-код не получен.")
    try:
        _tiktok().exchange_code(code)
    except Exception as error:
        raise HTTPException(400, f"Не удалось завершить авторизацию: {error}") from error
    return "<html><body><h2>TikTok подключён. Вернитесь в KLIPANI и обновите страницу.</h2></body></html>"


@app.post("/api/clips/{clip_id}/publish-tiktok")
def publish_clip_tiktok(clip_id: str, body: PublishRequest) -> dict:
    clip = get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Клип не найден.")
    provider = _tiktok()
    if not provider.is_connected():
        raise HTTPException(409, "TikTok не подключён. Пройдите OAuth-авторизацию.")
    try:
        result = provider.publish(
            clip["output_path"],
            PublishMetadata(title=body.title, description=body.description, privacy=body.privacy),
        )
    except RuntimeError as error:
        raise HTTPException(502, str(error)) from error
    return save_publication(clip_id, result.provider, result.external_id, result.url)


@app.post("/api/clips/{clip_id}/send-telegram")
def send_clip_telegram(clip_id: str) -> dict:
    from .services.telegram_send import recipient_chat_ids, send_clip

    clip = get_clip(clip_id)
    if not clip:
        raise HTTPException(404, "Клип не найден.")
    if not recipient_chat_ids():
        raise HTTPException(409, "Telegram не привязан: узнайте Chat ID командой /myid в боте и впишите в профиле.")
    try:
        result = send_clip(clip["output_path"], caption=f"🎬 {clip_id[:6]}")
    except RuntimeError as error:
        raise HTTPException(502, str(error)) from error
    return result


@app.get("/api/payments/donationalerts/status")
def da_status() -> dict:
    from .services import dapayments as da

    connected = da.is_connected()
    return {"provider": "donationalerts", "configured": da.is_configured(), "connected": connected}


@app.get("/api/payments/donationalerts/auth-url")
def da_auth_url() -> dict:
    from .services import dapayments as da

    try:
        return {"url": da.auth_url()}
    except RuntimeError as error:
        raise HTTPException(400, str(error)) from error


@app.get("/api/payments/donationalerts/callback", response_class=HTMLResponse)
def da_callback(code: Optional[str] = None) -> str:
    from .services import dapayments as da

    if not code:
        raise HTTPException(400, "OAuth-код не получен.")
    try:
        da.exchange_code(code)
    except Exception as error:
        raise HTTPException(400, f"Не удалось завершить авторизацию: {error}") from error
    return "<html><body><h2>DonationAlerts подключён. Поллер донатов запущен.</h2></body></html>"


@app.post("/api/bot/webhook")
async def telegram_webhook(request: Request) -> dict:
    settings = get_settings()
    if settings.telegram_webhook_secret:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret != settings.telegram_webhook_secret:
            raise HTTPException(403, "Bad webhook secret.")
    dispatcher = getattr(app.state, "dispatcher", None)
    bot = getattr(app.state, "bot", None)
    if dispatcher is None or bot is None:
        raise HTTPException(503, "Webhook-режим не активен.")
    from aiogram.types import Update

    try:
        update = Update.model_validate(await request.json())
        await dispatcher.feed_update(bot, update)
    except Exception as error:
        # Never 500 to Telegram: a failing update would be retried in a loop.
        logger.warning("webhook update failed: %r", error)
    return {"ok": True}


@app.post("/api/payments/donationalerts/check")
def da_check() -> dict:
    from .services import dapayments as da

    if not da.is_connected():
        raise HTTPException(409, "DonationAlerts не подключён.")
    return {"issued": da.poll_once()}
