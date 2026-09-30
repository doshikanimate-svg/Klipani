import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from huggingface_hub import try_to_load_from_cache
from huggingface_hub.utils import EntryNotFoundError

from ..config import get_settings
from ..db import database, one

ProgressCallback = Optional[Callable[[str, int], None]]

ALLOWED_MODELS = {"tiny", "base", "small", "medium"}
MODEL_REPO = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}


def _model_name() -> str:
    settings = get_settings()
    return settings.whisper_model if settings.whisper_model in ALLOWED_MODELS else "base"


def model_cached(name: Optional[str] = None) -> bool:
    repo = MODEL_REPO[_model_name() if name is None else name]
    try:
        path = try_to_load_from_cache(repo_id=repo, filename="model.bin")
    except EntryNotFoundError:
        return False
    return isinstance(path, str) and Path(path).exists()


def _load_model(name: str, timeout: float):
    """Load Whisper in a worker thread so a hung HuggingFace download can time out."""
    box: dict = {}

    def worker() -> None:
        try:
            from faster_whisper import WhisperModel

            box["model"] = WhisperModel(name, device="cpu", compute_type="int8")
        except Exception as error:  # noqa: BLE001 — surfaced to caller via box
            box["error"] = error

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    if thread.is_alive():
        raise TimeoutError(
            f"Загрузка модели Whisper ({name}) превысила {int(timeout)} с. "
            "Проверьте доступ к huggingface.co или поставьте WHISPER_MODEL=base/tiny."
        )
    if "error" in box:
        raise box["error"]
    return box["model"]


def transcribe(video: dict, on_progress: ProgressCallback = None, cancel_check: Optional[Callable[[], bool]] = None) -> list[dict]:
    """Run locally with a compact CPU-safe model; model weights download once from HuggingFace."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError as error:
        raise RuntimeError("faster-whisper не установлен. Выполните pip install -r backend/requirements.txt.") from error

    settings = get_settings()
    name = _model_name()
    if cancel_check and cancel_check():
        raise RuntimeError("Обработка отменена пользователем.")

    if not model_cached(name):
        raise RuntimeError(
            f"Модель Whisper ({name}) ещё не скачана. Загрузка с HuggingFace сейчас недоступна "
            "(часто из‑за VPN). Анализ продолжается без распознавания речи. "
            f"Чтобы включить Whisper, без VPN выполните: "
            f'python -c "from faster_whisper import WhisperModel; WhisperModel(\'{name}\')"'
        )

    if on_progress:
        on_progress("LOADING_MODEL", 30)
    try:
        model = _load_model(name, timeout=60.0)
    except TimeoutError as error:
        raise RuntimeError(str(error)) from error
    except Exception as error:
        raise RuntimeError("Не удалось загрузить модель Whisper. Проверьте сеть и кэш HuggingFace.") from error

    if cancel_check and cancel_check():
        raise RuntimeError("Обработка отменена пользователем.")
    if on_progress:
        on_progress("TRANSCRIBING", 40)

    def _is_caps_garbage(text: str) -> bool:
        """Whisper hallucinates on music/silence in ALL CAPS («МУЗЫКАЛЬНАЯ ЗАСТАВКА»)."""
        letters = [c for c in text if c.isalpha()]
        return bool(letters) and sum(c.isupper() for c in letters) / len(letters) > 0.7

    def _run_transcribe(language, vad: bool):
        source_segments, info = model.transcribe(
            str(video["path"]),
            language=language,
            vad_filter=vad,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        segments = []
        for segment in source_segments:
            text = segment.text.strip()
            if not text:
                continue
            words = [
                {"start": round(word.start, 3), "end": round(word.end, 3), "text": word.word.strip()}
                for word in (segment.words or [])
                if word.word.strip()
            ]
            segments.append({
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": text,
                "words": words,
            })
        coverage = sum(len(item["text"]) for item in segments if not _is_caps_garbage(item["text"]))
        return segments, (info.language or None), coverage

    try:
        # Forced-language decoding on mismatched audio is unstable: it may return
        # nothing or a plausible-looking garbage fragment. Try the configured
        # language and auto-detection, keep the pass with the best text coverage.
        forced_lang = settings.whisper_language or None
        candidates = [_run_transcribe(forced_lang, True)]
        if forced_lang is not None:
            candidates.append(_run_transcribe(None, True))
        best_segments, best_lang, best_coverage = max(candidates, key=lambda item: item[2])
        if best_coverage < 100:
            # VAD can discard synthetic/TTS speech entirely; retry without it
            # in both languages and keep the best coverage.
            candidates.append(_run_transcribe(forced_lang, False))
            if forced_lang is not None:
                candidates.append(_run_transcribe(None, False))
            best_segments, best_lang, best_coverage = max(candidates, key=lambda item: item[2])
        segments, info_lang = best_segments, best_lang
    except Exception as error:
        raise RuntimeError("Не удалось распознать речь. Проверьте аудиодорожку и свободную память.") from error

    # Drop junk before persisting: punctuation-only fragments and ALL-CAPS
    # music/silence hallucinations must not reach the LLM or the transcript file.
    segments = [
        item for item in segments
        if any(c.isalpha() for c in item["text"]) and not _is_caps_garbage(item["text"])
    ]

    if cancel_check and cancel_check():
        raise RuntimeError("Обработка отменена пользователем.")
    if not segments:
        raise RuntimeError("В видео не найдена распознаваемая речь.")

    destination = settings.storage / "transcripts" / f"{video['id']}.json"
    destination.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    record = {
        "id": uuid4().hex,
        "video_id": video["id"],
        "path": str(destination),
        "language": info_lang or settings.whisper_language,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with database() as db:
        db.execute("DELETE FROM transcripts WHERE video_id=?", (video["id"],))
        db.execute("INSERT INTO transcripts VALUES (:id,:video_id,:path,:language,:created_at)", record)
    return segments


def get_transcript(video_id: str) -> Optional[dict]:
    with database() as db:
        row = one(db.execute("SELECT * FROM transcripts WHERE video_id=?", (video_id,)).fetchone())
    if not row:
        return None
    path = Path(row["path"])
    try:
        segments = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        segments = []
    return {**row, "segments": segments}


def installed() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False
