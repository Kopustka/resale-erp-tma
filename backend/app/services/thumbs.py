"""Миниатюры фото с кэшем на диске.

Список склада показывал оригиналы с телефона — по 2–3 МБ на строку
высотой 124 пикселя. Шесть вещей давали около 15 МБ трафика, и на
мобильном это и есть та самая «неплавность».

Ширины из белого списка: произвольные значения из запроса раздули бы
кэш и дали бы вектор для его переполнения.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from ..config import get_settings

settings = get_settings()
log = logging.getLogger("thumbs")

# 400 хватает карточке списка при тройной плотности пикселей,
# 1200 — галерее в детали.
ALLOWED_WIDTHS = (400, 1200)
DEFAULT_QUALITY = 82

_CACHE_DIR = Path(settings.media_dir) / ".thumbs"


def cache_dir() -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR


def normalize_width(raw: int | None) -> int | None:
    """None — отдать оригинал. Иначе ближайшая разрешённая ширина."""
    if raw is None:
        return None
    return min(ALLOWED_WIDTHS, key=lambda w: abs(w - raw))


def _render(data: bytes, width: int) -> bytes | None:
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(data)) as src:
            # Тот же EXIF-разворот, что и в водяном знаке: без него
            # вертикальные снимки лягут набок.
            img = ImageOps.exif_transpose(src).convert("RGB")
            if img.width > width:
                height = round(img.height * width / img.width)
                img = img.resize((width, height), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=DEFAULT_QUALITY, optimize=True)
            return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        log.warning("thumb render failed: %s", e)
        return None


def for_local(name: str, width: int) -> Path | None:
    """Путь к миниатюре локального файла. Строит при первом обращении."""
    src = Path(settings.media_dir) / name
    if not src.exists():
        return None
    out = cache_dir() / f"{Path(name).stem}_{width}.jpg"
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    rendered = _render(src.read_bytes(), width)
    if rendered is None:
        return None
    out.write_bytes(rendered)
    return out


def for_bytes(key: str, data: bytes, width: int) -> bytes:
    """Миниатюра для фото из Telegram. Кэшируется по ключу file_id."""
    safe = "".join(c for c in key if c.isalnum() or c in "-_")[:60]
    out = cache_dir() / f"tg_{safe}_{width}.jpg"
    if out.exists():
        return out.read_bytes()
    rendered = _render(data, width)
    if rendered is None:
        return data  # не смогли уменьшить — лучше оригинал, чем ничего
    out.write_bytes(rendered)
    return rendered
