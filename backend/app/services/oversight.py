"""Подключение чужого склада к админ-панели наблюдателя.

Сценарий — тестировщик или подопечный, который ведёт отдельный склад, но
показывает свою работу. Членством такое не выразить: членство даёт право
менять чужие вещи, а нужно только чтение журнала.

Подключение требует согласия владельца склада.

Раньше оно происходило сразу: договорённость между двумя знакомыми людьми
складывается вне программы, и переспрашивать кнопкой казалось лишним. Но
приложение многоскладовое, и запрос принимает ЛЮБОЙ юзернейм — значит
любой владелец мог подключить к своей панели чужой склад, зная только ник,
и читать чужую ленту действий. Одна договорённость не может открывать
дверь всем остальным.

Поэтому запись создаётся в PENDING, а владелец подтверждает её кнопкой в
боте. Если имя ещё незнакомо боту, запрос дождётся первого /start.

Наблюдателю отдаётся лента действий и счётчики. Финансы (закупка, прибыль,
маржа) не отдаются и здесь: своя себестоимость остаётся своей.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import (
    OversightStatus,
    Role,
    Store,
    StoreMember,
    StoreOversight,
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
        "<b>Что увидит:</b> какие вещи вы добавляете, как меняете этапы, "
        "публикации и счётчики.\n"
        "<b>Что не увидит:</b> закупочные цены, прибыль и маржу. Менять "
        "ничего не сможет.\n\n"
        "Закрыть доступ в любой момент — /nadzor"
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
    """Отправить запрос адресату, если он уже знаком боту."""
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


async def decline(session: AsyncSession, req: StoreOversight) -> None:
    req.status = OversightStatus.DECLINED
    req.decided_at = datetime.now(timezone.utc)


async def notify_watcher(
    session: AsyncSession, req: StoreOversight, allowed: bool, store_name: str | None = None
) -> None:
    """Сообщить просившему решение, чтобы он не гадал, почему склада нет."""
    watcher = (
        await session.execute(select(User).where(User.id == req.watcher_id))
    ).scalar_one_or_none()
    if watcher is None:
        return
    who = f"@{req.target_username}"
    await _send(
        watcher.telegram_id,
        f"✅ {who} открыл вам ленту склада «{store_name}»."
        if allowed
        else f"✖️ {who} отклонил запрос на просмотр ленты.",
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


async def bind(
    session: AsyncSession, req: StoreOversight, target: User
) -> Store | None:
    """Привязать запись к складу адресата и включить наблюдение.

    Возвращает None, если склада у человека ещё нет — тогда запись остаётся
    в PENDING и привяжется позже.
    """
    store_id = await owned_store_id(session, target)
    if store_id is None:
        return None
    req.store_id = store_id
    req.status = OversightStatus.ACTIVE
    req.decided_at = datetime.now(timezone.utc)
    return (
        await session.execute(select(Store).where(Store.id == store_id))
    ).scalar_one()


async def ask_pending(session: AsyncSession, user: User) -> int:
    """Показать человеку запросы, дождавшиеся его первого /start.

    Раньше здесь была автоматическая привязка. Она и создавала дыру:
    достаточно было отправить запрос на чужой ник, чтобы получить доступ,
    как только человек впервые откроет бота.
    """
    waiting = await pending_for(session, user)
    sent = 0
    for req in waiting:
        if await deliver(session, req):
            sent += 1
    return sent


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
