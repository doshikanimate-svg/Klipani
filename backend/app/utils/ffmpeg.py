import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


class VideoToolError(RuntimeError):
    pass


def available(command: str) -> bool:
    return shutil.which(command) is not None


def bundled_exe() -> Optional[str]:
    """Full-featured static FFmpeg (with libass) shipped via imageio-ffmpeg."""
    try:
        import imageio_ffmpeg

        path = imageio_ffmpeg.get_ffmpeg_exe()
        return path if Path(path).exists() else None
    except ImportError:
        return None


def _escape_subtitles_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace("'", r"\'").replace(":", r"\:")


def _logo_stage(src: str, mark: dict, height: int, logo_alpha: Optional[float], tag: str) -> str:
    """Overlay a scaled logo onto [src], then drawtext it. Ends with [{tag}].

    `src` is a stream label, e.g. ``[base]``.
    """
    logo_filter = f",scale=-2:{height}"
    if logo_alpha is not None:
        logo_filter += f",format=rgba,colorchannelmixer=aa={logo_alpha}"
    return (
        f"movie='{_escape_subtitles_path(mark['logo'])}'{logo_filter}[{tag}g];"
        f"[{src}][{tag}g]overlay={mark['overlay']}[{tag}od];"
        f"[{tag}od]{mark['draw']}[{tag}]"
    )


def _text_stage(src: str, mark: dict, tag: str) -> str:
    """drawtext-only mark on [src]. Ends with [{tag}]."""
    return f"[{src}]{mark['draw']}[{tag}]"


def _apply_watermark(video_part: str, watermark: Optional[dict], service: Optional[dict] = None) -> str:
    """Append the free-tier service mark and the user watermark; ends with [vout].

    Each mark is its own labelled stage reading the previous label, so both can
    coexist and no stage ever re-consumes a stream that is already in use.
    """
    from ..services.effect_service import WATERMARK_LOGO_HEIGHT

    # video_part is always a plain filter chain (e.g. `[0:v]scale=...,setsar=1`),
    # never a bare label, so `null` is appended as another filter.
    stages: list[str] = [f"{video_part},null[base]"]
    current = "base"
    for mark, tag, height, alpha in (
        (service, "svc", None, None),
        (watermark, "wm", WATERMARK_LOGO_HEIGHT, None),
    ):
        if not mark:
            continue
        if mark.get("logo"):
            if tag == "svc":
                from ..services.service_watermark import SERVICE_LOGO_ALPHA, SERVICE_LOGO_HEIGHT

                height, alpha = SERVICE_LOGO_HEIGHT, SERVICE_LOGO_ALPHA
            stages.append(_logo_stage(current, mark, height, alpha, tag))
        else:
            stages.append(_text_stage(current, mark, tag))
        current = tag
    stages.append(f"[{current}]null[vout]")
    return ";".join(stages)


def sfx_inputs(sounds: list) -> list[str]:
    """Extra lavfi inputs for synthesized SFX (validated SoundSpec objects)."""
    from ..services.effect_service import synth_source

    args: list[str] = []
    for sound in sounds:
        args += ["-f", "lavfi", "-i", synth_source(sound)]
    return args


def sfx_mix_chain(base_label: str, sounds: list, first_index: int, duration: float, fade_duration: float = 0.4) -> str:
    """Delay each SFX to its timestamp, mix with the base audio, apply fades. Ends with [aout]."""
    parts: list[str] = []
    mix_inputs = [base_label]
    for offset, sound in enumerate(sounds):
        label = f"[sfx{offset}]"
        delay_ms = int(round(float(sound.at) * 1000))
        parts.append(f"[{first_index + offset}:a]adelay={delay_ms}|{delay_ms}{label}")
        mix_inputs.append(label)
    mixed = "".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:normalize=0[mix]"
    parts.append(mixed)
    fade_duration = min(fade_duration, max(0.0, duration / 2))
    if fade_duration > 0:
        parts.append(
            f"[mix]afade=t=in:st=0:d={fade_duration:.2f},"
            f"afade=t=out:st={max(0.0, duration - fade_duration):.2f}:d={fade_duration:.2f}[aout]"
        )
    else:
        parts.append("[mix]anull[aout]")
    return ";".join(parts)


def probe(path: Path) -> dict:
    if not available("ffprobe"):
        raise VideoToolError("FFmpeg/ffprobe не найден. Установите его командой: brew install ffmpeg")
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise VideoToolError("Не удалось прочитать видео. Проверьте, что файл не повреждён.")
    raw = json.loads(result.stdout)
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not video:
        raise VideoToolError("В файле не найдена видеодорожка.")
    fps_value = video.get("avg_frame_rate", "0/1")
    numerator, denominator = fps_value.split("/")
    fps = float(numerator) / float(denominator) if float(denominator) else 0
    return {
        "duration": float(raw.get("format", {}).get("duration", 0)),
        "width": int(video.get("width", 0)),
        "height": int(video.get("height", 0)),
        "fps": round(fps, 3),
        "codec": video.get("codec_name"),
        "audio_streams": len(audio),
        "audio_codec": audio[0].get("codec_name") if audio else None,
    }


def _blur_base() -> str:
    return (
        "split[vbg][vfg],"
        "[vbg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=40[bg],"
        "[vfg]scale=1080:-2[fg],"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def render_montage(
    source: Path,
    target: Path,
    parts: list[tuple[float, float]],
    subtitles_path: Optional[Path] = None,
    fonts_dir: str = "/System/Library/Fonts",
    vf_video: Optional[str] = None,
    af_chain: Optional[str] = None,
    sounds: Optional[list] = None,
    style: str = "crop",
    watermark: Optional[dict] = None,
    service: Optional[dict] = None,
) -> None:
    """Concatenate source windows, then crop to vertical. Single FFmpeg pass."""
    binary = bundled_exe() or ("ffmpeg" if available("ffmpeg") else None)
    if not binary:
        raise VideoToolError("FFmpeg не найден. Установите его командой: brew install ffmpeg")
    if (subtitles_path is not None or sounds or style == "blur" or watermark or service) and binary == "ffmpeg":
        bundled = bundled_exe()
        if not bundled:
            raise VideoToolError("Для прожига субтитров нужен imageio-ffmpeg: pip install -r backend/requirements.txt")
        binary = bundled
    if style not in ("crop", "blur"):
        raise VideoToolError("Неизвестный стиль экспорта.")
    if not parts:
        raise VideoToolError("Пустой монтаж: нет частей для склейки.")
    total = sum(max(0.2, end - start) for start, end in parts)
    inputs: list[str] = []
    for start, end in parts:
        duration = max(0.2, end - start)
        inputs += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source)]
    if sounds:
        inputs += sfx_inputs(sounds)
    stream_labels = "".join(f"[{i}:v][{i}:a?]" for i in range(len(parts)))
    if style == "blur":
        from ..services.effect_service import fade_suffix

        post_concat = f"{_blur_base()}{fade_suffix(total)}"
    else:
        post_concat = vf_video or (
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
        )
    video_chain = (
        f"{stream_labels}concat=n={len(parts)}:v=1:a=1[vcat][acat],"
        f"[vcat]{post_concat}"
    )
    if subtitles_path is not None:
        video_chain += f",subtitles={_escape_subtitles_path(subtitles_path)}:fontsdir='{fonts_dir}'"
    video_chain = _apply_watermark(video_chain, watermark, service)
    if sounds:
        audio_graph = sfx_mix_chain("[acat]", sounds, len(parts), total)
        full_graph = f"{video_chain};{audio_graph}"
        audio_map = "[aout]"
    elif af_chain:
        full_graph = f"{video_chain};[acat]{af_chain}[aout]"
        audio_map = "[aout]"
    else:
        full_graph = video_chain
        audio_map = "[acat]"
    command = [
        binary, "-y", *inputs,
        "-filter_complex", full_graph,
        "-map", "[vout]", "-map", audio_map,
        "-t", f"{total:.3f}",
    ]
    command += ["-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        logger.warning("render_montage failed: %s", (result.stderr or "")[-500:])
        raise VideoToolError("Не удалось собрать монтаж через FFmpeg. Подробности сохранены в логе сервера.")


def render_vertical(
    source: Path,
    target: Path,
    start: float,
    duration: float,
    subtitles_path: Optional[Path] = None,
    fonts_dir: str = "/System/Library/Fonts",
    vf_video: Optional[str] = None,
    af_chain: Optional[str] = None,
    sounds: Optional[list] = None,
    style: str = "crop",
    watermark: Optional[dict] = None,
    service: Optional[dict] = None,
) -> None:
    if subtitles_path is not None or sounds or style == "blur" or watermark or service:
        binary = bundled_exe()
        if not binary:
            raise VideoToolError("Для прожига субтитров нужен imageio-ffmpeg: pip install -r backend/requirements.txt")
    else:
        if not available("ffmpeg"):
            raise VideoToolError("FFmpeg не найден. Установите его командой: brew install ffmpeg")
        binary = "ffmpeg"
    if style not in ("crop", "blur"):
        raise VideoToolError("Неизвестный стиль экспорта.")
    inputs = ["-ss", f"{start:.3f}", "-i", str(source)]
    if style == "blur" or sounds or watermark or service:
        from ..services.effect_service import fade_suffix

        inputs += sfx_inputs(sounds or [])
        if style == "blur":
            # Shake crop is incompatible with the blur layout; fade is kept.
            video_part = f"[0:v]{_blur_base()}{fade_suffix(duration)}"
        else:
            # Scale to fill 9:16 then crop centrally. Values are fixed, never supplied by an LLM/user shell string.
            base = vf_video or "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
            video_part = f"[0:v]{base}"
        if subtitles_path is not None:
            video_part += f",subtitles={_escape_subtitles_path(subtitles_path)}:fontsdir='{fonts_dir}'"
        video_part = _apply_watermark(video_part, watermark, service)
        if sounds:
            full_graph = f"{video_part};{sfx_mix_chain('[0:a]', sounds, 1, duration)}"
            audio_map = "[aout]"
        elif af_chain:
            full_graph = f"{video_part};[0:a]{af_chain}[aout]"
            audio_map = "[aout]"
        else:
            full_graph = video_part
            audio_map = "0:a?"
        command = [binary, "-y", *inputs, "-filter_complex", full_graph, "-map", "[vout]", "-map", audio_map]
    else:
        # Scale to fill 9:16 then crop centrally. Values are fixed, never supplied by an LLM/user shell string.
        filter_graph = vf_video or "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
        if subtitles_path is not None:
            filter_graph += f",subtitles={_escape_subtitles_path(subtitles_path)}:fontsdir='{fonts_dir}'"
        command = [
            binary, "-y", *inputs,
            "-map", "0:v:0", "-map", "0:a?",
        ]
        if af_chain:
            command += ["-vf", filter_graph, "-af", af_chain]
        else:
            command += ["-vf", filter_graph]
    command += ["-t", f"{duration:.3f}", "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        logger.warning("render_vertical failed: %s", (result.stderr or "")[-500:])
        raise VideoToolError("Не удалось создать клип через FFmpeg. Подробности сохранены в логе сервера.")


def create_thumbnail(source: Path, target: Path) -> None:
    command = ["ffmpeg", "-y", "-ss", "0.5", "-i", str(source), "-frames:v", "1", "-vf", "scale=480:-2", str(target)]
    subprocess.run(command, capture_output=True, text=True, check=False)


def mean_volume(source: Path, start: float, duration: float) -> Optional[float]:
    """Return mean volume in dB via volumedetect, or None if measurement fails."""
    if not available("ffmpeg"):
        return None
    command = [
        "ffmpeg", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source),
        "-vn", "-af", "volumedetect", "-f", "null", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    for line in (result.stderr or "").splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip())
            except (IndexError, ValueError):
                return None
    return None
