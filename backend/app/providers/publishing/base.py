from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, Field


class PublishMetadata(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=5000)
    privacy: str = Field(default="unlisted", pattern="^(private|unlisted|public)$")
    category_id: str = Field(default="20")


class PublishResult(BaseModel):
    provider: str
    external_id: str
    url: str


class PublishingProvider(ABC):
    name: str = "base"

    @abstractmethod
    def is_configured(self) -> bool:
        """Client secrets present (OAuth can start)."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Valid user token stored."""

    @abstractmethod
    def auth_url(self) -> str:
        """OAuth consent URL to open in the browser."""

    @abstractmethod
    def exchange_code(self, code: str) -> None:
        """Exchange OAuth code for tokens and persist them."""

    @abstractmethod
    def publish(self, video_path: str, metadata: PublishMetadata) -> PublishResult:
        """Upload a video file. Raises RuntimeError with a human message on failure."""

    def channel_title(self) -> Optional[str]:
        return None
