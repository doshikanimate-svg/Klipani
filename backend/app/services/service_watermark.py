"""Service watermark on free-tier renders.

Paid plans (LICENSE ...) get clean output. Trial / no-key users get the
service mark burned into every clip: the KLIPANI app logo plus @Klipani_bot,
bottom-right, small and semi-transparent so it never fights the subtitles.

The logo ships with the app (backend/app/assets/klipani_logo.png). If the file
is missing (source checkout without assets), we fall back to text-only mark so
the requirement "watermark on every free clip" still holds.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from ..config import get_settings

logger = logging.getLogger(__name__)

SERVICE_LOGO_HEIGHT = 44
SERVICE_TEXT_SIZE = 30
SERVICE_ALPHA = 0.55
SERVICE_LOGO_ALPHA = 0.4
SERVICE_BOTTOM = 60
SERVICE_RIGHT = 48
SERVICE_GAP = 10
SERVICE_FONT = "/System/Library/Fonts/Helvetica.ttc"
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
    for path in (SERVICE_FONT, SERVICE_FONT_LINUX):
        if Path(path).exists():
            return path
    return SERVICE_FONT


def service_spec(storage_dir: Path) -> Optional[dict]:
    """Overlay+drawtext spec for the service mark, or None if disabled.

    Layout: [app logo][gap][@Klipani_bot] on one line, bottom-right corner,
    semi-transparent so it stays under the captions.
    """
    settings = get_settings()
    if not settings.service_watermark:
        return None
    handle = (settings.service_watermark_text or "@Klipani_bot").strip()
    if not handle:
        return None
    logo = service_logo_png()
    textfile = Path(storage_dir) / "clips" / "service.svc.txt"
    textfile.parent.mkdir(parents=True, exist_ok=True)
    textfile.write_text(handle, encoding="utf-8")
    # FFmpeg's overlay filter knows only frame/overlay sizes, not the text width.
    # So the logo is pinned to the right margin and the handle is drawn
    # right-aligned to the logo's left edge — both stay on one line.
    text_h = int(SERVICE_TEXT_SIZE * 1.35)
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
        f":borderw=2:bordercolor=black@{SERVICE_ALPHA}:x={text_x}:y={text_y}"
    )
    return {"draw": draw, "overlay": overlay, "logo": logo}


def needs_service_watermark() -> bool:
    """True when the current license is free-tier (trial / expired / none)."""
    from .license_service import current_state

    return bool(current_state().get("free"))