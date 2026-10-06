"""Pre-rendered service badge (free-tier watermark).

The mark is drawn once into a rounded "pill" PNG — app logo on the left, the
handle in bold next to it — and cached on disk. FFmpeg then only has to overlay
a single transparent image, which is both prettier and cheaper than compositing
a logo plus a drawtext chain.

Pillow is optional: without it we fall back to the logo+drawtext chain, so the
"watermark on every free clip" rule still holds on a stripped install.
"""

import hashlib
import logging
from pathlib import Path
from typing import Optional

from ..config import get_settings

logger = logging.getLogger(__name__)

# Rendered at 3x and downscaled by FFmpeg for clean edges.
BADGE_HEIGHT = 104
BADGE_SCALE = 3
BADGE_ALPHA = 0.82
BADGE_PADDING = 26
BADGE_GAP = 16
BADGE_RADIUS = 34
BADGE_TEXT_SIZE = 46

BADGE_TEXT_COLOR = (255, 255, 255, 255)
BADGE_BG_TOP = (38, 38, 50, 234)
BADGE_BG_BOTTOM = (14, 14, 20, 234)
BADGE_BORDER = (255, 255, 255, 70)
ACCENT_TILE = (255, 255, 255, 40)

FONT_CANDIDATES = (
    "/Library/Fonts/Roboto-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)

# Where the badge sits: bottom-right, clear of TikTok/Shorts captions.
BADGE_BOTTOM = 92
BADGE_RIGHT = 56


def _font(size: int):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _rounded_mask(size: tuple, radius: int):
    from PIL import Image, ImageDraw

    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255)
    return mask


def _gradient(size: tuple):
    from PIL import Image

    width, height = size
    image = Image.new("RGB", (1, height))
    pixels = image.load()
    for row in range(height):
        ratio = row / max(1, height - 1)
        pixels[0, row] = tuple(
            round(top + (bottom - top) * ratio) for top, bottom in zip(BADGE_BG_TOP[:3], BADGE_BG_BOTTOM[:3])
        )
    return image.resize((width, height))


def _fit_logo(logo_path: Path, height: int):
    """Logo scaled to `height`, with transparent rounded corners."""
    from PIL import Image

    logo = Image.open(logo_path).convert("RGBA")
    ratio = height / logo.height
    logo = logo.resize((max(1, round(logo.width * ratio)), height), Image.LANCZOS)
    # The bundled icon is an opaque rounded square; make the corners see-through
    # so the badge looks like one pill instead of a box inside a box.
    alpha = logo.getchannel("A")
    if alpha.getextrema() == (255, 255):
        corners = _rounded_mask(logo.size, max(8, logo.height // 5))
        logo.putalpha(corners)
    return logo


def render_badge(logo_path: Optional[Path], handle: str, out_path: Path) -> Optional[Path]:
    """Draw logo + handle into a pill PNG. Returns out_path, or None on failure."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("Pillow unavailable, service badge falls back to drawtext.")
        return None

    scale = BADGE_SCALE
    font = _font(BADGE_TEXT_SIZE * scale)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text_box = probe.textbbox((0, 0), handle, font=font)
    text_w = text_box[2] - text_box[0]
    text_h = text_box[3] - text_box[1]

    logo_h = BADGE_HEIGHT * scale
    logo = _fit_logo(logo_path, logo_h) if logo_path is not None else None
    logo_w = logo.width if logo is not None else 0

    pad = BADGE_PADDING * scale
    gap = BADGE_GAP * scale
    # The accent tile grows the logo, so the pill needs room for it on every side.
    tile_inset = 4 * scale if logo is not None else 0
    logo_block = logo_w + 2 * tile_inset if logo is not None else 0
    width = pad + logo_block + (gap if logo is not None else 0) + text_w + pad
    height = max(logo_h + 2 * tile_inset, text_h + (2 * pad) // 2)
    size = (width, height)

    badge = _gradient(size).convert("RGBA")
    badge.putalpha(_rounded_mask(size, BADGE_RADIUS * scale))
    # Soft inner highlight so the pill reads as a button, not a black box.
    ImageDraw.Draw(badge).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1),
        radius=BADGE_RADIUS * scale,
        outline=BADGE_BORDER,
        width=max(1, 2 * scale),
    )

    x = pad
    y = (height - logo_h - 2 * tile_inset) // 2
    draw = ImageDraw.Draw(badge)
    if logo is not None:
        # The app icon is a near-black rounded square; on a dark pill it would
        # read as a hole, so it sits on a faint brand-tinted tile.
        draw.rounded_rectangle(
            (x, y, x + logo_block, y + logo_h + 2 * tile_inset),
            radius=max(8, logo_h // 4),
            fill=ACCENT_TILE,
        )
        badge.alpha_composite(logo, (x + tile_inset, y + tile_inset))
        x += logo_block + gap
    # Centre the glyph box vertically against the whole logo block.
    text_y = height // 2 - text_h // 2 - text_box[1]
    draw.text((x, text_y), handle, font=font, fill=BADGE_TEXT_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    badge.save(out_path, format="PNG")
    return out_path


def badge_path(storage_dir: Path, logo: Optional[Path], handle: str) -> Optional[Path]:
    """Cached badge for this (logo, handle) pair; re-rendered when missing."""
    digest = hashlib.sha256(f"{handle}|{logo}|{BADGE_HEIGHT}|{BADGE_TEXT_SIZE}".encode()).hexdigest()[:12]
    target = Path(storage_dir) / "watermarks" / f"service-{digest}.png"
    if target.is_file() and target.stat().st_size > 0:
        return target
    try:
        return render_badge(logo, handle, target)
    except Exception as error:  # noqa: BLE001 — decoration must never break a render
        logger.warning("Service badge render failed: %s", error)
        return None


def badge_spec(storage_dir: Path) -> Optional[dict]:
    """Overlay spec for the pre-rendered badge, or None when unavailable."""
    from .service_watermark import service_logo_png

    settings = get_settings()
    handle = (settings.service_watermark_text or "@Klipani_bot").strip()
    if not handle:
        return None
    badge = badge_path(Path(storage_dir), service_logo_png(), handle)
    if badge is None:
        return None
    return {
        "logo": badge,
        "overlay": f"W-w-{BADGE_RIGHT}:H-h-{BADGE_BOTTOM}",
        "draw": "",
        "prebuilt": True,
    }