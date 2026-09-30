import json
import logging
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from ...config import ROOT, get_settings
from .base import PublishMetadata, PublishResult, PublishingProvider

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


class YouTubeProvider(PublishingProvider):
    name = "youtube"

    def _secrets_path(self) -> Path:
        return _resolve(get_settings().youtube_client_secrets)

    def _token_path(self) -> Path:
        return _resolve(get_settings().youtube_token_path)

    def _redirect_uri(self) -> str:
        settings = get_settings()
        return f"http://{settings.backend_host}:{settings.backend_port}/api/publish/youtube/callback"

    def is_configured(self) -> bool:
        return self._secrets_path().exists()

    def is_connected(self) -> bool:
        try:
            credentials = self._load_credentials()
        except Exception:
            return False
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                try:
                    credentials.refresh(GoogleRequest())
                    self._save_credentials(credentials)
                    return True
                except Exception as error:
                    logger.warning("YouTube token refresh failed: %s", error)
                    return False
            return False
        return True

    def auth_url(self) -> str:
        if not self.is_configured():
            raise RuntimeError(
                "Нет OAuth-ключей YouTube. Скачайте client_secrets в Google Cloud Console "
                f"и положите в {self._secrets_path()} (см. README)."
            )
        flow = Flow.from_client_secrets_file(
            str(self._secrets_path()), scopes=SCOPES, redirect_uri=self._redirect_uri()
        )
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        return url

    def exchange_code(self, code: str) -> None:
        flow = Flow.from_client_secrets_file(
            str(self._secrets_path()), scopes=SCOPES, redirect_uri=self._redirect_uri()
        )
        flow.fetch_token(code=code)
        self._save_credentials(flow.credentials)

    def channel_title(self) -> Optional[str]:
        try:
            service = self._service()
            response = service.channels().list(part="snippet", mine=True).execute()
            items = response.get("items", [])
            return items[0]["snippet"]["title"] if items else None
        except Exception as error:
            logger.debug("YouTube channel lookup failed: %s", error)
            return None

    def publish(self, video_path: str, metadata: PublishMetadata) -> PublishResult:
        path = Path(video_path)
        if not path.exists():
            raise RuntimeError("Файл клипа не найден.")
        try:
            service = self._service()
            media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True, chunksize=8 * 1024 * 1024)
            body = {
                "snippet": {
                    "title": metadata.title,
                    "description": metadata.description,
                    "categoryId": metadata.category_id,
                },
                "status": {"privacyStatus": metadata.privacy, "selfDeclaredMadeForKids": False},
            }
            request = service.videos().insert(part="snippet,status", body=body, media_body=media)
            response = None
            while response is None:
                _, response = request.next_chunk()
            video_id = response["id"]
            return PublishResult(
                provider=self.name,
                external_id=video_id,
                url=f"https://www.youtube.com/shorts/{video_id}",
            )
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError(f"Загрузка на YouTube не удалась: {error}") from error

    def _load_credentials(self) -> Optional[Credentials]:
        token_path = self._token_path()
        if not token_path.exists():
            return None
        data = json.loads(token_path.read_text(encoding="utf-8"))
        return Credentials.from_authorized_user_info(data, SCOPES)

    def _save_credentials(self, credentials: Credentials) -> None:
        token_path = self._token_path()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(credentials.to_json(), encoding="utf-8")
        try:
            token_path.chmod(0o600)
        except OSError:
            pass

    def _service(self):
        credentials = self._load_credentials()
        if not credentials:
            raise RuntimeError("YouTube не подключён. Пройдите OAuth-авторизацию.")
        if not credentials.valid and credentials.expired and credentials.refresh_token:
            credentials.refresh(GoogleRequest())
            self._save_credentials(credentials)
        return build("youtube", "v3", credentials=credentials)
