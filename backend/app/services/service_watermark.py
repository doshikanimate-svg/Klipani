"""Service watermark on free-tier renders.

Paid plans (LICENSE ...) get clean output. Trial / no-key users get the service
mark burned into every clip: the KLIPANI app logo plus @Klipani_bot, bottom-right
as one pill, semi-transparent so it never fights the subtitles.

The mark itself is pre-rendered by badge_service (Pillow). The logo ships with
the app (backend/app/assets/klipani_logo.png); without Pillow or without the
logo we degrade to a drawtext handle, never to no mark at all.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from ..config import get_settings
from .badge_service import badge_spec

logger = logging.getLogger(__name__)

# Fallback-only values, used when Pillow is missing and the badge cannot be baked.
SERVICE_LOGO_HEIGHT = 60
SERVICE_TEXT_SIZE = 40
SERVICE_ALPHA = 0.8
SERVICE_LOGO_ALPHA = 0.9
SERVICE_BOTTOM = 92
SERVICE_RIGHT = 56
SERVICE_GAP = 16
SERVICE_FONT = "/Library/Fonts/Roboto-Bold.ttf"
SERVICE_FONT_ALT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
SERVICE_FONT_LINUX = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def service_logo_png() -> Optional[Path]:
    """Find the bundled KLIPANI logo, or None when it is not available."""
    override = os.environ.get("KLIPANI_LOGO", "")
    candidates = []
    if override:
        candidates.append(Path(override))
    # Frozen (PyInstaller) first: assets sit next to the exe.
    try:
        import sys

        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates += [
                exe_dir / "_internal" / "assets" / "klipani_logo.png",
                exe_dir / "assets" / "klipani_logo.png",
            ]
    except Exception:  # noqa: BLE001
        pass
    candidates += [
        _project_root() / "backend" / "app" / "assets" / "klipani_logo.png",
        _project_root() / "app" / "assets" / "klipani_logo.png",
    ]
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _service_font() -> str:
    for path in (SERVICE_FONT, SERVICE_FONT_ALT, SERVICE_FONT_LINUX):
        if Path(path).exists():
            return path
    return SERVICE_FONT


def service_spec(storage_dir: Path) -> Optional[dict]:
    """Overlay spec for the service mark, or None if disabled.

    Preferred path is the pre-rendered pill badge (logo + handle side by side,
    bold, bottom-right). Without Pillow we fall back to a logo overlay plus a
    drawtext handle, so the mark is never silently dropped.
    """
    settings = get_settings()
    if not settings.service_watermark:
        return None
    handle = (settings.service_watermark_text or "@Klipani_bot").strip()
    if not handle:
        return None
    badge = badge_spec(Path(storage_dir))
    if badge is not None:
        return badge
    logo = service_logo_png()
    textfile = Path(storage_dir) / "clips" / "service.svc.txt"
    textfile.parent.mkdir(parents=True, exist_ok=True)
    textfile.write_text(handle, encoding="utf-8")
    # FFmpeg's overlay filter knows only frame/overlay sizes, not the text width.
    # So the logo is pinned to the right margin and the handle is drawn
    # right-aligned to the logo's left edge — both stay on one line.
    text_h = int(SERVICE_TEXT_SIZE * 1.3)
    group_h = max(SERVICE_LOGO_HEIGHT, text_h)
    # overlay's uppercase H/W is the main frame, lowercase h/w the logo itself.
    group_y = f"H-h-{group_h + SERVICE_BOTTOM}"
    if logo is not None:
        from .effect_service import logo_display_width

        logo_w = logo_display_width(logo, SERVICE_LOGO_HEIGHT)
        overlay = f"W-w-{SERVICE_RIGHT}:{group_y}"
        text_x = f"w-text_w-{SERVICE_RIGHT + logo_w + SERVICE_GAP}"
        text_y = f"h-{SERVICE_BOTTOM}-{group_h - (group_h - text_h) // 2}"
    else:
        overlay = ""
        text_x = f"w-text_w-{SERVICE_RIGHT}"
        text_y = group_y
    draw = (
        f"drawtext=fontfile='{_service_font()}':textfile='{textfile}'"
        f":fontsize={SERVICE_TEXT_SIZE}:fontcolor=white@{SERVICE_ALPHA}"
        f":borderw=3:bordercolor=black@{SERVICE_ALPHA}:x={text_x}:y={text_y}"
    )
    return {"draw": draw, "overlay": overlay, "logo": logo}


def needs_service_watermark() -> bool:
    """True when the current license is free-tier (trial / expired / none)."""
    from .license_service import current_state

    return bool(current_state().get("free"))