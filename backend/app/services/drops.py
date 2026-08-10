"""Публикация подборки вещей одним альбомом.

Подпись у медиагруппы одна и висит на первом фото, поэтому она собирается
как список: заголовок, затем строка на каждую вещь с ценой и размером.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Drop, DropItem, Item
from .post_template import esc

settings = get_settings()
LOCAL_PREFIX = "local:"
MAX_ITEMS = 10


async def load(session: AsyncSession, drop_id: uuid.UUID) -> tuple[Drop | None, list[Item]]:
    drop = (
        await session.execute(select(Drop).where(Drop.id == drop_id))
    ).scalar_one_or_none()
    if drop is None:
        return None, []
    rows = (
        await session.execute(
            select(Item)
            .join(DropItem, DropItem.item_id == Item.id)
            .where(DropItem.drop_id == drop_id)
            .order_by(DropItem.position)
        )
    ).scalars().all()
    return drop, list(rows)


def build_caption(drop: Drop, items: list[Item], signature: str | None = None) -> str:
    """Заголовок + нумерованный список вещей. Номера совпадают с фото."""
    from .fx import symbol as cur_symbol

    lines: list[str] = []
    title = (drop.title or "Новый дроп").strip()
    lines.append(f"<b>{esc(title)}</b>")
    note = (drop.note or "").strip()
    if note:
        lines.append("")
        lines.append(esc(note))
    lines.append("")
    for i, it in enumerate(items, 1):
        price = it.list_price_orig if it.list_price_orig is not None else it.selling_price_orig
        bits = [esc((it.title or it.brand or "Вещь").strip())]
        if it.size:
            bits.append(esc(it.size))
        if price is not None:
            f = float(price)
            num = str(int(f)) if f == int(f) else str(f)
            bits.append(f"{num} {cur_symbol(it.price_currency or 'BYN')}")
        lines.append(f"{i}. " + " · ".join(bits))
    sig = (signature or "").strip()
    if sig:
        lines.append("")
        lines.append(esc(sig))
    return "\n".join(lines)[:1024]


def photo_entries(items: list[Item], watermark_text: str | None) -> list[tuple[str | None, bytes | None]]:
    """По одному фото с вещи: (file_id, байты). Вещи без фото пропускаем."""
    out: list[tuple[str | None, bytes | None]] = []
    for it in items[:MAX_ITEMS]:
        photos = list(it.photo_file_ids or [])
        if not photos:
            continue
        entry = photos[0]
        if entry.startswith(LOCAL_PREFIX):
            name = entry[len(LOCAL_PREFIX):]
            if "/" in name or "\\" in name or ".." in name:
                continue
            path = Path(settings.media_dir) / name
            if not path.exists():
                continue
            data = path.read_bytes()
            if watermark_text:
                from .watermark import apply as apply_watermark

                data = apply_watermark(data, watermark_text)
            out.append((None, data))
        else:
            out.append((entry, None))
    return out
