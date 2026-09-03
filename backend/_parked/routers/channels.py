"""Каналы автопостинга: несколько на склад, у каждого своя подпись."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import OWNER_ONLY, get_active_membership, require_role
from ..db import get_session
from ..models import Channel, ItemPost, StoreMember
from ..schemas import ChannelCreate, ChannelOut, ChannelUpdateOne
from ..services import telegram_post
from ..services import audit

router = APIRouter(prefix="/api/v1/channels", tags=["channels"])


def _normalize(raw: str) -> str:
    """@username, t.me/name или числовой -100… — приводим к виду для Bot API."""
    v = (raw or "").strip()
    if not v:
        raise HTTPException(422, "Пустой адрес канала")
    low = v.lower()
    for pref in ("https://", "http://"):
        if low.startswith(pref):
            v, low = v[len(pref):], low[len(pref):]
    for pref in ("t.me/", "telegram.me/"):
        if low.startswith(pref):
            rest = v[len(pref):]
            # Инвайт-ссылку t.me/+hash в @username превращать нельзя.
            return v if rest.startswith("+") else f"@{rest.strip('/')}"
    if v.startswith("@") or v.lstrip("-").isdigit():
        return v
    return f"@{v}"


async def _counts(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(ItemPost.channel_id, func.count(ItemPost.id))
            .where(ItemPost.channel_id.in_(ids))
            .group_by(ItemPost.channel_id)
        )
    ).all()
    return {cid: n for cid, n in rows}


def _out(ch: Channel, posts: int) -> ChannelOut:
    return ChannelOut(
        id=ch.id,
        chat_id=ch.chat_id,
        title=ch.title,
        signature=ch.signature,
        enabled=ch.enabled,
        posts_count=posts,
    )


async def _owned(session: AsyncSession, store_id: uuid.UUID, cid: uuid.UUID) -> Channel:
    ch = (
        await session.execute(
            select(Channel).where(Channel.id == cid, Channel.store_id == store_id)
        )
    ).scalar_one_or_none()
    if ch is None:
        raise HTTPException(404, "Channel not found")
    return ch


@router.get("", response_model=list[ChannelOut])
async def list_channels(
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(Channel)
            .where(Channel.store_id == member.store_id)
            .order_by(Channel.created_at)
        )
    ).scalars().all()
    counts = await _counts(session, [c.id for c in rows])
    return [_out(c, counts.get(c.id, 0)) for c in rows]


@router.post("", response_model=ChannelOut, status_code=201)
async def create_channel(
    payload: ChannelCreate,
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    chat_id = _normalize(payload.chat_id)
    dup = (
        await session.execute(
            select(Channel.id).where(
                Channel.store_id == member.store_id, Channel.chat_id == chat_id
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(409, "Такой канал уже добавлен")
    ch = Channel(
        store_id=member.store_id,
        chat_id=chat_id,
        title=(payload.title or "").strip() or None,
        signature=(payload.signature or "").strip() or None,
        enabled=True,
    )
    session.add(ch)
    await session.flush()
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.CHANNEL_ADD,
        summary=ch.title or ch.chat_id,
        entity_type="channel",
        entity_id=ch.id,
    )
    await session.commit()
    await session.refresh(ch)
    return _out(ch, 0)


@router.patch("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: uuid.UUID,
    payload: ChannelUpdateOne,
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    ch = await _owned(session, member.store_id, channel_id)
    if payload.title is not None:
        ch.title = payload.title.strip() or None
    if payload.signature is not None:
        ch.signature = payload.signature.strip() or None
    if payload.enabled is not None:
        ch.enabled = payload.enabled
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.CHANNEL_EDIT,
        summary=ch.title or ch.chat_id,
        entity_type="channel",
        entity_id=ch.id,
    )
    await session.commit()
    await session.refresh(ch)
    counts = await _counts(session, [ch.id])
    return _out(ch, counts.get(ch.id, 0))


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: uuid.UUID,
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    ch = await _owned(session, member.store_id, channel_id)
    # Записи о постах удаляем вместе с каналом: без канала они бессмысленны,
    # а внешний ключ иначе не даст удалить.
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.CHANNEL_DELETE,
        summary=ch.title or ch.chat_id,
        entity_type="channel",
        entity_id=None,
    )
    await session.execute(ItemPost.__table__.delete().where(ItemPost.channel_id == ch.id))
    await session.delete(ch)
    await session.commit()


@router.post("/{channel_id}/test")
async def test_channel(
    channel_id: uuid.UUID,
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    ch = await _owned(session, member.store_id, channel_id)
    try:
        await telegram_post.send_test(ch.chat_id)
    except telegram_post.ChannelError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}
