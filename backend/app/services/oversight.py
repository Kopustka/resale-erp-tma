"""Подключение чужого склада к админ-панели наблюдателя.

Сценарий — тестировщик или подопечный, который ведёт отдельный склад, но
показывает свою работу. Членством такое не выразить: членство даёт право
менять чужие вещи, а нужно только чтение журнала.

Подключение происходит сразу, без подтверждения в боте: договорённость
между людьми складывается вне программы, и второй раз переспрашивать
кнопкой незачем. Если имя ещё незнакомо боту, запись ждёт в PENDING и
привязывается сама при первом /start — тогда у человека уже есть склад.

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


async def bind_pending_for(session: AsyncSession, user: User) -> int:
    """Дозамкнуть запросы, которые ждали появления склада у этого человека.

    Вызывается из /start сразу после создания личного склада. Без этого
    запрос, отправленный до первого запуска бота, висел бы в PENDING вечно.
    """
    if not user.username:
        return 0
    waiting = (
        await session.execute(
            select(StoreOversight).where(
                StoreOversight.target_username == user.username.lower(),
                StoreOversight.status == OversightStatus.PENDING,
            )
        )
    ).scalars().all()
    bound = 0
    for req in waiting:
        if await bind(session, req, user) is not None:
            bound += 1
    return bound


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
