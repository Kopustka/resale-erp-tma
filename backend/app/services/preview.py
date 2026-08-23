"""Предпросмотр в личке перед публикацией в канал.

Распространяется на всё, что создаёт новое сообщение в канале: карточку
вещи, пост контент-плана, объявление о скидке, дроп. Задания создаются в
статусе AWAITING — воркер их не забирает, пока владелец не подтвердит.

Подтверждение не отменяет расписание, а снимает блокировку: задание
переходит в PENDING со своим прежним run_after. Пост, назначенный на
20:00 и одобренный в 18:00, уйдёт всё равно в 20:00.

Шлём напрямую в Bot API, а не через aiogram: этот код выполняется в
процессе API, где диспетчера бота нет.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select

from ..config import get_settings

settings = get_settings()
log = logging.getLogger("preview")

API = f"https://api.telegram.org/bot{settings.bot_token}"
LOCAL_PREFIX = "local:"

APPROVE = "pub"
DECLINE = "no"

# Вид сущности в callback_data. По одной букве: лимит поля — 64 байта, а
# «pub:» + буква + uuid уже занимает 42.
ITEM = "i"
POST = "c"
DISCOUNT = "d"
DROP = "k"
BUMP = "b"

#: Как назвать публикуемое в заголовке предпросмотра.
KIND_TITLES = {
    ITEM: "Карточка вещи",
    POST: "Пост",
    DISCOUNT: "Объявление о скидке",
    DROP: "Дроп",
    BUMP: "Поднятие вещи",
}


def keyboard(entity_id: uuid.UUID, kind: str = ITEM) -> dict:
    """Кнопки подтверждения."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Опубликовать", "callback_data": f"{APPROVE}:{kind}:{entity_id}"},
                {"text": "✖️ Отмена", "callback_data": f"{DECLINE}:{kind}:{entity_id}"},
            ]
        ]
    }


def parse_callback(data: str) -> tuple[str, str, uuid.UUID] | None:
    """Разбирает callback_data кнопки: действие, вид сущности, id.

    Понимает и старый двухчастный формат «pub:<uuid>» без вида: кнопки,
    отправленные до этого изменения, могут висеть в переписке и обязаны
    продолжать работать — там это всегда была вещь.
    """
    parts = (data or "").split(":")
    if len(parts) == 2:
        action, raw = parts
        kind = ITEM
    elif len(parts) == 3:
        action, kind, raw = parts
    else:
        return None
    if action not in (APPROVE, DECLINE) or kind not in KIND_TITLES:
        return None
    try:
        return action, kind, uuid.UUID(raw)
    except ValueError:
        return None


async def is_enabled(session, store_id: uuid.UUID) -> bool:
    """Включён ли предпросмотр у склада."""
    from ..models import Store

    return bool(
        (
            await session.execute(
                select(Store.preview_before_post).where(Store.id == store_id)
            )
        ).scalar_one_or_none()
    )


def _load_local(entry: str) -> bytes | None:
    name = entry[len(LOCAL_PREFIX):]
    if "/" in name or "\\" in name or ".." in name:
        return None
    p = Path(settings.media_dir) / name
    return p.read_bytes() if p.exists() else None


def _head(kind: str, channels: int, when=None) -> str:
    """Шапка предпросмотра: что именно, куда и когда уйдёт.

    Время публикации показываем обязательно: подтверждение не публикует
    немедленно, и без этой строки владелец ждал бы поста сразу.
    """
    title = KIND_TITLES.get(kind, "Публикация")
    when_line = (
        f"выйдет {when.astimezone().strftime('%d.%m в %H:%M')}"
        if when is not None
        else "выйдет сразу после подтверждения"
    )
    return (
        f"👀 <b>Предпросмотр</b> · {title}\n"
        f"Каналов: {channels} · {when_line}\n\n"
    )


async def send_entity_preview(
    chat_id: int,
    kind: str,
    entity_id: uuid.UUID,
    body: str,
    photo_entry: str | None,
    channels: int,
    watermark_text: str | None = None,
    when=None,
) -> bool:
    """Предпросмотр любой публикации. False — доставить не удалось."""
    return await _send(
        chat_id,
        keyboard(entity_id, kind),
        (_head(kind, channels, when) + body),
        photo_entry,
        watermark_text,
    )


async def send_preview(
    chat_id: int,
    item_id: uuid.UUID,
    caption: str,
    photo_entry: str | None,
    channels: int,
    watermark_text: str | None = None,
) -> bool:
    """Предпросмотр карточки вещи (публикуется сразу после подтверждения)."""
    return await send_entity_preview(
        chat_id, ITEM, item_id, caption, photo_entry, channels, watermark_text
    )


#: Пределы Telegram: подпись под фото короче обычного сообщения.
CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096


async def _send(
    chat_id: int,
    kb: dict,
    text: str,
    photo_entry: str | None,
    watermark_text: str | None = None,
) -> bool:
    # Режем по тому же пределу, что и настоящая публикация: предпросмотр,
    # обрезанный сильнее поста, показывал бы не то, что уйдёт в канал.
    text = text[: CAPTION_LIMIT if photo_entry else TEXT_LIMIT]

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
