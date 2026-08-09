"""Водяной знак на фото, уходящих в канал.

Оригиналы в хранилище не трогаем: знак накладывается на копию в момент
публикации. Так вещь можно перевыставить с другой подписью, а исходник
остаётся чистым для маркетплейсов.

Текст берётся из подписи канала (Store.channel_signature).
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from ..config import get_settings

settings = get_settings()
log = logging.getLogger("watermark")

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
)

# Доля от меньшей стороны кадра — чтобы знак читался и на вертикальных фото.
FONT_SCALE = 0.045
MIN_FONT_PX = 14
MARGIN_SCALE = 0.025
TEXT_OPACITY = 165        # ~65% — заметно, но не забивает вещь
SHADOW_OPACITY = 90
MAX_SIDE = 1600           # заодно ужимаем гигантские снимки с телефона


def _font(size: int):
    from PIL import ImageFont

    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def apply(image_bytes: bytes, text: str) -> bytes:
    """Возвращает JPEG с подписью в правом нижнем углу.

    При любой ошибке отдаёт исходные байты: пост важнее водяного знака.
    """
    text = (text or "").strip()
    if not text:
        return image_bytes
    try:
        from PIL import Image, ImageDraw

        with Image.open(io.BytesIO(image_bytes)) as src:
            img = src.convert("RGB")
            if max(img.size) > MAX_SIDE:
                img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

            w, h = img.size
            size = max(MIN_FONT_PX, int(min(w, h) * FONT_SCALE))
            font = _font(size)
            margin = max(6, int(min(w, h) * MARGIN_SCALE))

            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            box = draw.textbbox((0, 0), text, font=font)
            tw, th = box[2] - box[0], box[3] - box[1]
            x = w - tw - margin - box[0]
            y = h - th - margin - box[1]

            # Тень — чтобы текст читался и на светлом, и на тёмном фоне.
            draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, SHADOW_OPACITY))
            draw.text((x, y), text, font=font, fill=(255, 255, 255, TEXT_OPACITY))

            out = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
            buf = io.BytesIO()
            out.save(buf, format="JPEG", quality=88, optimize=True)
            return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        log.warning("watermark skipped: %s", e)
        return image_bytes
