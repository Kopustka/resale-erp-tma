"""Подборки: публикация нескольких вещей одним альбомом."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CAN_EDIT, get_active_membership
from ..db import get_session
from ..models import Channel, Drop, DropItem, Item, JobKind, StoreMember
from ..schemas import DropCreate, DropOut
from ..services import post_queue
from ..services.drops import MAX_ITEMS

router = APIRouter(prefix="/api/v1/drops", tags=["drops"])


@router.post("", response_model=DropOut, status_code=201)
async def create_drop(
    payload: DropCreate,
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    """Собирает подборку и ставит её публикацию во все включённые каналы."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot publish")

    ids = list(dict.fromkeys(payload.item_ids))  # порядок важен, дубли убираем
    if not ids:
        raise HTTPException(422, "Не выбрано ни одной вещи")
    if len(ids) > MAX_ITEMS:
        raise HTTPException(422, f"В альбом Telegram влезает не больше {MAX_ITEMS} фото")

    items = (
        await session.execute(
            select(Item).where(Item.id.in_(ids), Item.store_id == member.store_id)
        )
    ).scalars().all()
    found = {i.id: i for i in items}
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise HTTPException(404, f"Вещи не найдены: {', '.join(missing)}")

    with_photo = [i for i in ids if (found[i].photo_file_ids or [])]
    if not with_photo:
        raise HTTPException(422, "Ни у одной вещи нет фото — альбом собрать не из чего")

    drop = Drop(
        store_id=member.store_id,
        title=(payload.title or "").strip() or None,
        note=(payload.note or "").strip() or None,
    )
    session.add(drop)
    await session.flush()
    for pos, item_id in enumerate(ids):
        session.add(DropItem(drop_id=drop.id, item_id=item_id, position=pos))

    channels = (
        await session.execute(
            select(Channel).where(
                Channel.store_id == member.store_id, Channel.enabled.is_(True)
            )
        )
    ).scalars().all()
    for ch in channels:
        await post_queue.enqueue(
            session,
            store_id=member.store_id,
            kind=JobKind.DROP_POST,
            channel_id=ch.chat_id,
            channel_uid=ch.id,
            drop_id=drop.id,
        )
    await session.commit()

    return DropOut(
        id=drop.id,
        title=drop.title,
        item_count=len(ids),
        with_photo=len(with_photo),
        channels=len(channels),
    )
