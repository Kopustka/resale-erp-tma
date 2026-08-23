"""Надзор за чужим складом: запрос, согласие, отзыв.

Модель доверия здесь простая и намеренно жёсткая: наблюдение существует
только пока владелец наблюдаемого склада не против. Он подтверждает его
кнопкой в боте и может отозвать в любой момент, не спрашивая наблюдателя.

Наблюдателю отдаётся лента действий и счётчики — то же, что владелец видит
про своих сотрудников. Финансы (закупка, прибыль, маржа) не отдаются:
человек ведёт свой бизнес, и его себестоимость наблюдателя не касается.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import (
    OversightStatus,
    Store,
    StoreMember,
    StoreOversight,
    Role,
    User,
)

settings = get_settings()
log = logging.getLogger("oversight")

API = f"https://api.telegram.org/bot{settings.bot_token}"

ACCEPT = "ovok"
DECLINE = "ovno"


def keyboard(req_id: uuid.UUID) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Разрешить", "callback_data": f"{ACCEPT}:{req_id}"},
                {"text": "✖️ Отказать", "callback_data": f"{DECLINE}:{req_id}"},
            ]
        ]
    }


def request_text(watcher: User) -> str:
    """Что именно спрашивают. Формулировка должна быть честной до мелочей."""
    name = watcher.first_name or (f"@{watcher.username}" if watcher.username else "Владелец")
    handle = f" (@{watcher.username})" if watcher.username else ""
    return (
        f"👀 <b>{name}</b>{handle} просит доступ к ленте действий вашего склада.\n\n"
        "<b>Что он увидит:</b>\n"
        "• какие вещи вы добавляете и как меняете их статусы\n"
        "• публикации в канал, скидки, дропы\n"
        "• счётчики: сколько добавлено, выставлено, отправлено\n\n"
        "<b>Что он НЕ увидит:</b>\n"
        "• закупочные цены, прибыль и маржу\n"
        "• он не сможет ничего менять в вашем складе\n\n"
        "Отозвать доступ можно в любой момент командой /nadzor."
    )


async def _send(chat_id: int, text: str, kb: dict | None = None) -> bool:
    """Шлём напрямую в Bot API: код исполняется в процессе API, где нет диспетчера."""
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if kb is not None:
        payload["reply_markup"] = kb
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"{API}/sendMessage", json=payload)
            data = r.json()
            if not data.get("ok"):
                log.warning("надзор: не доставлено %s: %s", chat_id, data.get("description"))
            return bool(data.get("ok"))
    except Exception as e:  # noqa: BLE001
        log.warning("надзор: ошибка отправки: %s", e)
        return False


async def deliver(session: AsyncSession, req: StoreOversight) -> bool:
    """Отправить запрос адресату, если он уже знаком боту.

    Если нет — запрос просто ждёт в PENDING, и бот покажет его при первом
    /start. Возвращает True, если сообщение ушло.
    """
    target = (
        await session.execute(
            select(User).where(User.username == req.target_username)
        )
    ).scalar_one_or_none()
    if target is None:
        return False
    watcher = (
        await session.execute(select(User).where(User.id == req.watcher_id))
    ).scalar_one()
    return await _send(target.telegram_id, request_text(watcher), keyboard(req.id))


async def pending_for(session: AsyncSession, user: User) -> list[StoreOversight]:
    """Непринятые запросы к этому человеку."""
    if not user.username:
        return []
    return list(
        (
            await session.execute(
                select(StoreOversight).where(
                    StoreOversight.target_username == user.username.lower(),
                    StoreOversight.status == OversightStatus.PENDING,
                )
            )
        ).scalars()
    )


async def owned_store_id(session: AsyncSession, user: User) -> uuid.UUID | None:
    """Склад, которым человек владеет. Активный — в приоритете."""
    rows = (
        await session.execute(
            select(StoreMember.store_id).where(
                StoreMember.user_id == user.id, StoreMember.role == Role.OWNER
            )
        )
    ).scalars().all()
    if not rows:
        return None
    if user.current_store_id in rows:
        return user.current_store_id
    return rows[0]


async def accept(
    session: AsyncSession, req: StoreOversight, target: User
) -> Store | None:
    """Согласие: привязываем запрос к складу адресата и включаем наблюдение."""
    store_id = await owned_store_id(session, target)
    if store_id is None:
        return None
    req.store_id = store_id
    req.status = OversightStatus.ACTIVE
    req.decided_at = datetime.now(timezone.utc)
    return (
        await session.execute(select(Store).where(Store.id == store_id))
    ).scalar_one()


async def decline(session: AsyncSession, req: StoreOversight) -> None:
    req.status = OversightStatus.DECLINED
    req.decided_at = datetime.now(timezone.utc)


async def watched_store_ids(session: AsyncSession, watcher_id: uuid.UUID) -> list[uuid.UUID]:
    """Склады, которые этот наблюдатель вправе смотреть прямо сейчас."""
    return list(
        (
            await session.execute(
                select(StoreOversight.store_id).where(
                    StoreOversight.watcher_id == watcher_id,
                    StoreOversight.status == OversightStatus.ACTIVE,
                    StoreOversight.store_id.is_not(None),
                )
            )
        ).scalars()
    )


async def can_watch(
    session: AsyncSession, watcher_id: uuid.UUID, store_id: uuid.UUID
) -> bool:
    row = (
        await session.execute(
            select(StoreOversight.id).where(
                StoreOversight.watcher_id == watcher_id,
                StoreOversight.store_id == store_id,
                StoreOversight.status == OversightStatus.ACTIVE,
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def notify_watcher(session: AsyncSession, req: StoreOversight, allowed: bool,
                         store_name: str | None = None) -> None:
    """Сообщить наблюдателю решение — чтобы он не гадал, почему склада нет."""
    watcher = (
        await session.execute(select(User).where(User.id == req.watcher_id))
    ).scalar_one_or_none()
    if watcher is None:
        return
    who = f"@{req.target_username}"
    text = (
        f"✅ {who} открыл вам ленту действий склада «{store_name}»."
        if allowed
        else f"✖️ {who} отклонил запрос на просмотр ленты действий."
    )
    await _send(watcher.telegram_id, text)


async def notify_revoked(session: AsyncSession, req: StoreOversight, store_name: str) -> None:
    watcher = (
        await session.execute(select(User).where(User.id == req.watcher_id))
    ).scalar_one_or_none()
    if watcher is None:
        return
    await _send(
        watcher.telegram_id,
        f"🔒 Доступ к ленте склада «{store_name}» закрыт владельцем.",
    )
