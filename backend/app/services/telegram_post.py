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


_bot_username: str | None = None


async def get_bot_username() -> str | None:
    """@username бота — нужен для deep link из мини-аппа. Кэшируется."""
    global _bot_username
    if _bot_username is not None:
        return _bot_username
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{API}/getMe")
        data = r.json()
        if data.get("ok"):
            _bot_username = data["result"].get("username")
    except Exception as e:  # noqa: BLE001
        log.warning("getMe failed: %s", e)
    return _bot_username


def build_caption(
    item: dict, signature: str | None = None, template_body: str | None = None
) -> str:
    """Собирает подпись поста по шаблону склада.

    template_body=None — встроенное оформление (совпадает с тем, что было
    захардкожено до появления шаблонов).
    """
    from . import post_template as pt

    body = template_body or pt.DEFAULT_TEMPLATE_BODY
    return pt.render(body, pt.build_context(item, signature))


def _load_local(entry: str) -> bytes | None:
    name = entry[len(LOCAL_PREFIX):]
    if "/" in name or "\\" in name or ".." in name:
        return None
    p = Path(settings.media_dir) / name
    return p.read_bytes() if p.exists() else None


async def post_item(
    channel_id: str,
    item: dict,
    signature: str | None = None,
    template_body: str | None = None,
    watermark_text: str | None = None,
) -> int | None:
    """Публикует вещь. Возвращает message_id первого сообщения или None.

    watermark_text — если задан, накладывается на локальные фото (копия,
    оригинал в хранилище не меняется).
    """
    caption = build_caption(item, signature, template_body)
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
                if watermark_text:
                    from .watermark import apply as apply_watermark

                    data = apply_watermark(data, watermark_text)
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


async def edit_caption(
    channel_id: str,
    message_id: int,
    item: dict,
    signature: str | None = None,
    template_body: str | None = None,
    prefix: str = "",
) -> None:
    """Перерисовывает подпись поста. prefix — плашка сверху (ПРОДАНО, СКИДКА).

    У поста без фото подписи нет — там правится текст сообщения, иначе
    Bot API отвечает «there is no caption in the message to edit».
    """
    body = build_caption(item, signature, template_body)
    text = (prefix + body)[:1024] if prefix else body[:1024]
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{API}/editMessageCaption",
            json={
                "chat_id": channel_id,
                "message_id": message_id,
                "caption": text,
                "parse_mode": "HTML",
            },
        )
        data = r.json()
        if not data.get("ok") and "no caption" in str(data.get("description", "")).lower():
            r = await client.post(
                f"{API}/editMessageText",
                json={
                    "chat_id": channel_id,
                    "message_id": message_id,
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            data = r.json()
    if not data.get("ok"):
        desc = str(data.get("description", ""))
        # Текст не изменился — Telegram считает это ошибкой, для нас это норма.
        if "not modified" in desc.lower():
            return
        raise ChannelError(desc or f"HTTP {r.status_code}")


async def mark_sold(
    channel_id: str,
    message_id: int,
    item: dict,
    signature: str | None = None,
    template_body: str | None = None,
) -> None:
    """Добавляет к посту пометку ПРОДАНО."""
    await edit_caption(
        channel_id, message_id, item, signature, template_body,
        prefix="✅ <b>ПРОДАНО</b>\n\n",
    )


async def delete_message(channel_id: str, message_id: int) -> None:
    """Удаляет пост. Уже удалённое сообщение ошибкой не считаем."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{API}/deleteMessage",
            json={"chat_id": channel_id, "message_id": message_id},
        )
    data = r.json()
    if data.get("ok"):
        return
    desc = str(data.get("description", "")).lower()
    # Пост мог быть удалён руками или устареть — для бампа это не помеха.
    if "not found" in desc or "message to delete" in desc or "message can't be deleted" in desc:
        log.info("delete_message: %s", desc)
        return
    raise ChannelError(data.get("description", f"HTTP {r.status_code}"))


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
