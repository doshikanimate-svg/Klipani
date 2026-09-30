import json
import logging
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

from ...config import get_settings
from .base import PublishMetadata, PublishResult, PublishingProvider

logger = logging.getLogger(__name__)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

SCOPES = "user.info.basic,video.upload,video.publish"
CHUNK_SIZE = 10 * 1024 * 1024

PRIVACY_MAP = {
    "private": "SELF_ONLY",
    "unlisted": "SELF_ONLY",  # TikTok has no unlisted: closest is private
    "public": "PUBLIC_TO_EVERYONE",
}


class TikTokProvider(PublishingProvider):
    name = "tiktok"

    def _redirect_uri(self) -> str:
        settings = get_settings()
        return f"http://{settings.backend_host}:{settings.backend_port}/api/publish/tiktok/callback"

    def _token_path(self) -> Path:
        path = Path(get_settings().tiktok_token_path)
        return path if path.is_absolute() else get_settings().storage.parent / path

    def is_configured(self) -> bool:
        settings = get_settings()
        return bool(settings.tiktok_client_key and settings.tiktok_client_secret)

    def is_connected(self) -> bool:
        return self._access_token() is not None

    def auth_url(self) -> str:
        settings = get_settings()
        if not self.is_configured():
            raise RuntimeError(
                "Нет ключей TikTok. Создайте приложение на developers.tiktok.com "
                "(Login Kit + Content Posting API) и задайте TIKTOK_CLIENT_KEY/SECRET в .env."
            )
        query = urlencode({
            "client_key": settings.tiktok_client_key,
            "scope": SCOPES,
            "response_type": "code",
            "redirect_uri": self._redirect_uri(),
            "state": "klipani",
        })
        return f"{AUTH_URL}?{query}"

    def exchange_code(self, code: str) -> None:
        settings = get_settings()
        response = requests.post(TOKEN_URL, data={
            "client_key": settings.tiktok_client_key,
            "client_secret": settings.tiktok_client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self._redirect_uri(),
        }, timeout=30)
        self._raise_for_status(response)
        self._save_token(response.json())

    def channel_title(self) -> Optional[str]:
        return None  # would need user.info scopes + display API; skip

    def publish(self, video_path: str, metadata: PublishMetadata) -> PublishResult:
        path = Path(video_path)
        if not path.exists():
            raise RuntimeError("Файл клипа не найден.")
        token = self._access_token()
        if not token:
            raise RuntimeError("TikTok не подключён. Пройдите OAuth-авторизацию.")
        size = path.stat().st_size
        total_chunks = max(1, (size + CHUNK_SIZE - 1) // CHUNK_SIZE)
        try:
            publish_id, upload_url = self._init_post(
                token,
                title=metadata.title[:2200],
                privacy=PRIVACY_MAP.get(metadata.privacy, "SELF_ONLY"),
                size=size,
                total_chunks=total_chunks,
            )
            self._upload_chunks(upload_url, path, total_chunks)
            return PublishResult(
                provider=self.name,
                external_id=publish_id,
                url="https://www.tiktok.com/upload",
            )
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError(f"Загрузка в TikTok не удалась: {error}") from error

    # -- internals --

    def _headers(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}

    @staticmethod
    def _raise_for_status(response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            response.raise_for_status()
            raise RuntimeError("Пустой ответ TikTok API.")
        error = payload.get("error", {})
        if error.get("code") != "ok":
            raise RuntimeError(f"TikTok API: {error.get('message', payload)}")
        return payload.get("data", {})

    def _save_token(self, data: dict) -> None:
        record = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", ""),
            "open_id": data.get("open_id", ""),
            "expires_at": time.time() + int(data.get("expires_in", 86400)) - 300,
        }
        token_path = self._token_path()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(json.dumps(record), encoding="utf-8")
        try:
            token_path.chmod(0o600)
        except OSError:
            pass

    def _load_token(self) -> Optional[dict]:
        token_path = self._token_path()
        if not token_path.exists():
            return None
        try:
            return json.loads(token_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _access_token(self) -> Optional[str]:
        record = self._load_token()
        if not record or not record.get("access_token"):
            return None
        if time.time() < record.get("expires_at", 0):
            return record["access_token"]
        refresh_token = record.get("refresh_token")
        if not refresh_token:
            return None
        try:
            settings = get_settings()
            response = requests.post(TOKEN_URL, data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }, timeout=30)
            data = self._raise_for_status(response)
            merged = {**record, **data}
            self._save_token({
                "access_token": merged["access_token"],
                "refresh_token": merged.get("refresh_token", refresh_token),
                "open_id": merged.get("open_id", record.get("open_id", "")),
                "expires_in": data.get("expires_in", 86400),
            })
            return merged["access_token"]
        except Exception as error:
            logger.warning("TikTok token refresh failed: %s", error)
            return None

    def _init_post(self, token: str, title: str, privacy: str, size: int, total_chunks: int) -> tuple:
        response = requests.post(INIT_URL, headers=self._headers(token), json={
            "post_info": {"title": title, "privacy_level": privacy},
            "post_mode": "DIRECT_POST",
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": CHUNK_SIZE,
                "total_chunk_count": total_chunks,
            },
        }, timeout=60)
        data = self._raise_for_status(response)
        return data["publish_id"], data["upload_url"]

    def _upload_chunks(self, upload_url: str, path: Path, total_chunks: int) -> None:
        size = path.stat().st_size
        with path.open("rb") as stream:
            for index in range(total_chunks):
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                start = index * CHUNK_SIZE
                end = min(size, start + len(chunk)) - 1
                response = requests.put(
                    upload_url,
                    data=chunk,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{size}",
                        "Content-Type": "video/mp4",
                    },
                    timeout=300,
                )
                if response.status_code not in (200, 201, 206):
                    raise RuntimeError(f"Чанк {index + 1}/{total_chunks} не принят: HTTP {response.status_code}")
