from pathlib import Path
from uuid import uuid4

ALLOWED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm"}


def ensure_storage(storage: Path) -> None:
    for directory in ("uploads", "working", "clips", "thumbnails", "transcripts", "watermarks"):
        (storage / directory).mkdir(parents=True, exist_ok=True)


def safe_upload_name(filename: str) -> str:
    candidate = Path(filename or "video.mp4")
    extension = candidate.suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Поддерживаются только MP4, MKV, MOV и WEBM.")
    stem = "".join(c for c in candidate.stem if c.isalnum() or c in "-_ ").strip() or "video"
    return f"{uuid4().hex}_{stem[:100]}{extension}"
