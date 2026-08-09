"""Склады, переключение активного, управление командой и приглашения."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import OWNER_ONLY, get_active_membership, get_current_user, require_role
from ..db import get_session
from ..models import (
    Channel,
    InviteStatus,
    Role,
    Store,
    StoreInvite,
    StoreMember,
    User,
)
from ..auth import CAN_SEE_FINANCE
from ..schemas import (
    ChannelSettings,
    ChannelUpdate,
    FxRates,
    InviteCreate,
    InviteOut,
    MemberOut,
    StoreOut,
    StoreSettings,
    SwitchStore,
)
from ..services import fx, telegram_post

ALLOWED_CURRENCIES = ("BYN", "RUB", "USD", "EUR")

router = APIRouter(prefix="/api/v1/stores", tags=["stores"])


def _normalize_channel(raw: str | None) -> str | None:
    """Приводит ввод к тому, что понимает Bot API: @username или числовой -100… id.
    Публичный: @name или t.me/name. Приватный: числовой id (@ нет)."""
    if not raw:
        return None
    v = raw.strip()
    if not v:
        return None
    # числовой id канала (-100…) — как есть
    if v.lstrip("-").isdigit():
        return v
    # публичная ссылка t.me/name -> @name (инвайт-ссылки t.me/+… НЕ трогаем)
    if "t.me/" in v:
        tail = v.split("t.me/", 1)[1].strip("/")
        if tail.startswith("+") or tail.startswith("joinchat"):
            return v  # приватная инвайт-ссылка — Bot API её не примет (тест подскажет)
        v = "@" + tail
    if not v.startswith("@"):
        v = "@" + v
    return v


@router.get("", response_model=list[StoreOut])
async def my_stores(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(Store, StoreMember.role)
            .join(StoreMember, StoreMember.store_id == Store.id)
            .where(StoreMember.user_id == user.id)
        )
    ).all()
    return [
        StoreOut(id=s.id, name=s.name, role=role, base_currency=s.base_currency or "BYN")
        for s, role in rows
    ]


@router.post("/switch")
async def switch_store(
    payload: SwitchStore,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    member = (
        await session.execute(
            select(StoreMember).where(
                StoreMember.user_id == user.id,
                StoreMember.store_id == payload.store_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(403, "Not a member of this store")
    user.current_store_id = payload.store_id
    await session.commit()
    return {"ok": True, "current_store_id": str(payload.store_id)}


@router.get("/settings", response_model=StoreSettings)
async def get_settings_(
    member: StoreMember = Depends(require_role(*CAN_SEE_FINANCE)),
    session: AsyncSession = Depends(get_session),
):
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one()
    return StoreSettings(base_currency=store.base_currency or "BYN")


@router.patch("/settings", response_model=StoreSettings)
async def set_settings(
    payload: StoreSettings,
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    cur = payload.base_currency.upper()
    if cur not in ALLOWED_CURRENCIES:
        raise HTTPException(422, "Unsupported currency")
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one()
    store.base_currency = cur
    await session.commit()
    return StoreSettings(base_currency=cur)


@router.get("/fx", response_model=FxRates)
async def fx_rates(
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one()
    base = store.base_currency or "BYN"
    return FxRates(base=base, rates=await fx.rates_map(base))


@router.get("/channel", response_model=ChannelSettings)
async def get_channel(
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one()
    return ChannelSettings(
        channel_id=store.channel_id,
        channel_signature=store.channel_signature,
        watermark_enabled=store.watermark_enabled,
        watermark_text=store.watermark_text,
    )


@router.patch("/channel", response_model=ChannelSettings)
async def set_channel(
    payload: ChannelUpdate,
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    channel = _normalize_channel(payload.channel_id)
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one()
    store.channel_id = channel
    sig = (payload.channel_signature or "").strip()
    store.channel_signature = sig or None
    if payload.watermark_enabled is not None:
        store.watermark_enabled = payload.watermark_enabled
    if payload.watermark_text is not None:
        store.watermark_text = payload.watermark_text.strip() or None

    # Совместимость: постинг работает по таблице channels, а этот старый
    # эндпоинт правит поля склада. Держим их согласованными, иначе смена
    # канала в мини-аппе не влияла бы на публикацию.
    existing = (
        await session.execute(select(Channel).where(Channel.store_id == store.id))
    ).scalars().all()
    if channel:
        primary = next((c for c in existing if c.chat_id == channel), None)
        if primary is None:
            primary = Channel(store_id=store.id, chat_id=channel)
            session.add(primary)
        primary.enabled = True
        primary.signature = store.channel_signature
        # Прочие каналы, заведённые через старый экран, гасим: он одноканальный.
        for c in existing:
            if c.chat_id != channel:
                c.enabled = False
    else:
        for c in existing:
            c.enabled = False

    await session.commit()
    return ChannelSettings(
        channel_id=channel,
        channel_signature=store.channel_signature,
        watermark_enabled=store.watermark_enabled,
        watermark_text=store.watermark_text,
    )


@router.post("/channel/test")
async def test_channel(
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one()
    if not store.channel_id:
        raise HTTPException(422, "Канал не задан")
    try:
        await telegram_post.send_test(store.channel_id)
    except telegram_post.ChannelError as e:
        # Частые причины — бот не админ / неверный @username
        raise HTTPException(400, f"Не удалось отправить: {e}")
    return {"ok": True}


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(User, StoreMember.role)
            .join(StoreMember, StoreMember.user_id == User.id)
            .where(StoreMember.store_id == member.store_id)
        )
    ).all()
    return [
        MemberOut(user_id=u.id, username=u.username, first_name=u.first_name, role=role)
        for u, role in rows
    ]


@router.post("/invites", response_model=InviteOut, status_code=201)
async def invite_member(
    payload: InviteCreate,
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Пригласить по юзернейму. Если юзер уже писал боту — привязываем сразу."""
    if payload.role == Role.OWNER:
        raise HTTPException(422, "Cannot invite as OWNER")
    uname = payload.username.lstrip("@").lower()

    # Уже участник?
    existing_user = (
        await session.execute(select(User).where(User.username == uname))
    ).scalar_one_or_none()
    if existing_user:
        already = (
            await session.execute(
                select(StoreMember).where(
                    StoreMember.user_id == existing_user.id,
                    StoreMember.store_id == member.store_id,
                )
            )
        ).scalar_one_or_none()
        if already:
            raise HTTPException(409, "User already a member")
        # мгновенная привязка
        session.add(
            StoreMember(
                user_id=existing_user.id, store_id=member.store_id, role=payload.role
            )
        )
        inv = StoreInvite(
            store_id=member.store_id,
            username=uname,
            role=payload.role,
            invited_by=user.id,
            status=InviteStatus.ACCEPTED,
        )
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
        return InviteOut.model_validate(inv)

    # pending-инвайт (юзер ещё не контактировал с ботом)
    dup = (
        await session.execute(
            select(StoreInvite).where(
                StoreInvite.store_id == member.store_id,
                StoreInvite.username == uname,
                StoreInvite.status == InviteStatus.PENDING,
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "Invite already pending")

    inv = StoreInvite(
        store_id=member.store_id,
        username=uname,
        role=payload.role,
        invited_by=user.id,
        status=InviteStatus.PENDING,
    )
    session.add(inv)
    await session.commit()
    await session.refresh(inv)
    return InviteOut.model_validate(inv)


@router.delete("/invites/{invite_id}", status_code=204)
async def revoke_invite(
    invite_id: uuid.UUID,
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    inv = (
        await session.execute(
            select(StoreInvite).where(
                StoreInvite.id == invite_id,
                StoreInvite.store_id == member.store_id,
            )
        )
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(404, "Invite not found")
    inv.status = InviteStatus.REVOKED
    await session.commit()
