from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    app_env: str = "development"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    pre_roll_seconds: float = 10
    post_roll_seconds: float = 10
    max_clip_duration: float = 45
    max_highlights: int = 3
    min_highlight_distance: float = 30
    crop_anchor: str = "center"
    llm_provider: str = "mock"
    ollama_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3:1.7b"
    whisper_model: str = "base"
    whisper_language: str = "ru"
    youtube_client_secrets: str = "storage/yt_client.json"
    youtube_token_path: str = "storage/yt_token.json"
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_token_path: str = "storage/tt_token.json"
    telegram_bot_token: str = ""
    license_secret: str = ""
    telegram_webhook_secret: str = ""
    tg_admin_username: str = "LiveForWork1"
    crypto_wallet: str = ""
    public_url: str = ""
    da_client_id: str = ""
    da_client_secret: str = ""
    da_token_path: str = "storage/da_token.json"
    da_donate_url: str = ""
    klipani_data: str = ""

    @property
    def storage(self) -> Path:
        if self.klipani_data:
            return Path(self.klipani_data)
        return ROOT / "storage"


@lru_cache
def get_settings() -> Settings:
    return Settings()
