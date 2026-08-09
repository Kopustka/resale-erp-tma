"""Предпросмотр поста в личке перед публикацией в канал.

Задания создаются в статусе AWAITING — воркер их не забирает. Владельцу
уходит сообщение ровно в том виде, в каком пост появится в канале, с
кнопками «Опубликовать» и «Отмена». Нажатие переводит задания в PENDING
либо CANCELLED.

Шлём напрямую в Bot API, а не через aiogram: этот код выполняется в
процессе API, где диспетчера бота нет.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import httpx

from ..config import get_settings

settings = get_settings()
log = logging.getLogger("preview")

API = f"https://api.telegram.org/bot{settings.bot_token}"
LOCAL_PREFIX = "local:"

APPROVE = "pub"
DECLINE = "no"


def keyboard(item_id: uuid.UUID) -> dict:
    """Кнопки подтверждения. В callback_data 64 байта — uuid с префиксом влезает."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Опубликовать", "callback_data": f"{APPROVE}:{item_id}"},
                {"text": "✖️ Отмена", "callback_data": f"{DECLINE}:{item_id}"},
            ]
        ]
    }


def _load_local(entry: str) -> bytes | None:
    name = entry[len(LOCAL_PREFIX):]
    if "/" in name or "\\" in name or ".." in name:
        return None
    p = Path(settings.media_dir) / name
    return p.read_bytes() if p.exists() else None


async def send_preview(
    chat_id: int,
    item_id: uuid.UUID,
    caption: str,
    photo_entry: str | None,
    channels: int,
    watermark_text: str | None = None,
) -> bool:
    """Отправляет предпросмотр. Возвращает False, если отправить не вышло."""
    head = f"👀 <b>Предпросмотр</b> · каналов: {channels}\n\n"
    text = (head + caption)[:1024]
    kb = keyboard(item_id)

    try:
        async with httpx.AsyncClient(timeout=40) as client:
            if photo_entry and photo_entry.startswith(LOCAL_PREFIX):
                data = _load_local(photo_entry)
                if data is not None:
                    if watermark_text:
                        from .watermark import apply as apply_watermark

                        data = apply_watermark(data, watermark_text)
                    r = await client.post(
                        f"{API}/sendPhoto",
                        data={
                            "chat_id": chat_id,
                            "caption": text,
                            "parse_mode": "HTML",
                            "reply_markup": __import__("json").dumps(kb),
                        },
                        files={"photo": ("preview.jpg", data)},
                    )
                    return bool(r.json().get("ok"))
            r = await client.post(
                f"{API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": kb,
                },
            )
            ok = bool(r.json().get("ok"))
            if not ok:
                log.warning("preview failed: %s", r.text[:200])
            return ok
    except Exception as e:  # noqa: BLE001
        log.warning("preview error: %s", e)
        return False


UNSUB = "unsub"


async def notify_subscriber(
    chat_id: int,
    sub_id,
    post: dict,
    signature: str | None,
    template_body: str | None,
) -> bool:
    """Шлёт подписчику новинку. False — доставить не удалось (бот заблокирован)."""
    from .telegram_post import build_caption, item_link

    link = await item_link(post["id"]) if post.get("id") else None
    caption = "🔔 <b>Появилось по вашей подписке</b>\n\n" + build_caption(
        post, signature, template_body, link
    )
    kb = {
        "inline_keyboard": [
            [{"text": "🔕 Отписаться", "callback_data": f"{UNSUB}:{sub_id}"}]
        ]
    }
    photos = post.get("photo_file_ids") or []
    entry = photos[0] if photos else None
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            if entry and entry.startswith(LOCAL_PREFIX):
                data = _load_local(entry)
                if data is not None:
                    r = await client.post(
                        f"{API}/sendPhoto",
                        data={
                            "chat_id": chat_id,
                            "caption": caption[:1024],
                            "parse_mode": "HTML",
                            "reply_markup": __import__("json").dumps(kb),
                        },
                        files={"photo": ("item.jpg", data)},
                    )
                    return bool(r.json().get("ok"))
            r = await client.post(
                f"{API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": caption[:4096],
                    "parse_mode": "HTML",
                    "reply_markup": kb,
                },
            )
            data = r.json()
            if not data.get("ok"):
                log.info("подписчику %s не доставлено: %s", chat_id, data.get("description"))
            return bool(data.get("ok"))
    except Exception as e:  # noqa: BLE001
        log.warning("notify error: %s", e)
        return False
