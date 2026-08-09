"""Автопостинг вещей в Telegram-канал магазина.

Вызывается фоново при переходе вещи в LISTED. Собирает подпись (название,
описание, замеры, цена) и публикует фото в канал. При продаже — помечает
пост «ПРОДАНО». Сервер шлёт напрямую в Bot API (без aiogram).
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..config import get_settings

settings = get_settings()
log = logging.getLogger("channel")

API = f"https://api.telegram.org/bot{settings.bot_token}"
LOCAL_PREFIX = "local:"
MAX_MEDIA = 10


class ChannelError(Exception):
    pass


def _fmt_num(v) -> str:
    if v is None:
        return ""
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)


def build_caption(item: dict, signature: str | None = None) -> str:
    """Собирает подпись поста: название, описание, цена, замеры, контакт."""
    from .fx import symbol as cur_symbol

    lines: list[str] = []
    title = (item.get("title") or "").strip()
    if title:
        lines.append(f"<b>{_esc(title)}</b>")
    descr = (item.get("description") or "").strip()
    if descr:
        lines.append("")
        lines.append(_esc(descr))

    tail: list[str] = []
    price = item.get("price")
    if price is not None:
        sym = cur_symbol(item.get("price_currency") or "BYN")
        tail.append(f"💰 {_fmt_num(price)} {sym}")
    else:
        tail.append("💬 Цена — в личные сообщения")

    meas = []
    for label, key in (("Длина", "length_cm"), ("Ширина", "width_cm"), ("Рукав", "sleeve_cm")):
        val = _fmt_num(item.get(key))
        if val:
            meas.append(f"{label} {val}")
    if meas:
        tail.append("📐 " + " · ".join(meas))

    if tail:
        lines.append("")
        lines.extend(tail)

    sig = (signature or "").strip()
    if sig:
        lines.append("")
        lines.append(_esc(sig))
    return "\n".join(lines)[:1024]


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _load_local(entry: str) -> bytes | None:
    name = entry[len(LOCAL_PREFIX):]
    if "/" in name or "\\" in name or ".." in name:
        return None
    p = Path(settings.media_dir) / name
    return p.read_bytes() if p.exists() else None


async def post_item(channel_id: str, item: dict, signature: str | None = None) -> int | None:
    """Публикует вещь. Возвращает message_id первого сообщения или None."""
    caption = build_caption(item, signature)
    photos: list[str] = item.get("photo_file_ids") or []

    async with httpx.AsyncClient(timeout=40) as client:
        if not photos:
            r = await client.post(
                f"{API}/sendMessage",
                json={"chat_id": channel_id, "text": caption, "parse_mode": "HTML"},
            )
            return _msg_id(r)

        # Локальные файлы грузим как multipart, file_id ссылаем строкой.
        media: list[dict] = []
        files: dict[str, tuple[str, bytes]] = {}
        for i, entry in enumerate(photos[:MAX_MEDIA]):
            item_media: dict = {"type": "photo"}
            if entry.startswith(LOCAL_PREFIX):
                data = _load_local(entry)
                if data is None:
                    continue
                key = f"photo{i}"
                files[key] = (f"{key}.jpg", data)
                item_media["media"] = f"attach://{key}"
            else:
                item_media["media"] = entry  # telegram file_id
            if not media:  # подпись на первом фото
                item_media["caption"] = caption
                item_media["parse_mode"] = "HTML"
            media.append(item_media)

        if not media:
            r = await client.post(
                f"{API}/sendMessage",
                json={"chat_id": channel_id, "text": caption, "parse_mode": "HTML"},
            )
            return _msg_id(r)

        if len(media) == 1:
            m = media[0]
            data = {"chat_id": channel_id, "caption": caption, "parse_mode": "HTML"}
            if m["media"].startswith("attach://"):
                r = await client.post(f"{API}/sendPhoto", data=data, files={"photo": files["photo0"]})
            else:
                data["photo"] = m["media"]
                r = await client.post(f"{API}/sendPhoto", data=data)
            return _msg_id(r)

        # media group
        import json as _json

        r = await client.post(
            f"{API}/sendMediaGroup",
            data={"chat_id": channel_id, "media": _json.dumps(media)},
            files=files or None,
        )
        return _msg_id(r, group=True)


def _msg_id(r: httpx.Response, group: bool = False) -> int | None:
    data = r.json()
    if not data.get("ok"):
        raise ChannelError(data.get("description", f"HTTP {r.status_code}"))
    res = data["result"]
    if group:
        return res[0]["message_id"] if res else None
    return res["message_id"]


async def mark_sold(channel_id: str, message_id: int, item: dict, signature: str | None = None) -> None:
    """Добавляет к посту пометку ПРОДАНО (editMessageCaption)."""
    caption = "✅ <b>ПРОДАНО</b>\n\n" + build_caption(item, signature)
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{API}/editMessageCaption",
            json={
                "chat_id": channel_id,
                "message_id": message_id,
                "caption": caption[:1024],
                "parse_mode": "HTML",
            },
        )
    data = r.json()
    if not data.get("ok"):
        log.warning("mark_sold failed: %s", data.get("description"))


async def send_test(channel_id: str) -> None:
    """Проверка: бот шлёт тестовое сообщение в канал. Бросает ChannelError."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{API}/sendMessage",
            json={
                "chat_id": channel_id,
                "text": "✅ Автопостинг подключён. Выставленные вещи будут появляться здесь.",
            },
        )
    data = r.json()
    if not data.get("ok"):
        raise ChannelError(data.get("description", f"HTTP {r.status_code}"))
