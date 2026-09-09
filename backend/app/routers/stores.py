"""Склады, переключение активного, управление командой и приглашения."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import OWNER_ONLY, get_active_membership, get_current_user, require_role
from ..db import get_session
from ..models import (
    InviteStatus,
    Item,
    Role,
    Store,
    StoreInvite,
    StoreMember,
    User,
)
from ..auth import CAN_SEE_FINANCE
from ..schemas import (
    FxRates,
    InviteCreate,
    InviteOut,
    MemberOut,
    StoreOut,
    StoreSettings,
    SwitchStore,
)
from ..services import audit, fx

ALLOWED_CURRENCIES = ("BYN", "RUB", "USD", "EUR")

router = APIRouter(prefix="/api/v1/stores", tags=["stores"])


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
    was = (store.base_currency or "BYN").upper()
    store.base_currency = cur

    moved = 0
    if was != cur:
        # Пересчитываем уже заведённые вещи. Раньше менялась только надпись:
        # старые суммы оставались в прежней валюте, новые считались в новой,
        # и аналитика складывала рубли с долларами в одно число, ничем не
        # выдавая ошибку.
        #
        # Множитель один на всех и берётся на день переключения. Историю
        # он не искажает: суммы вещи масштабируются вместе, поэтому прибыль
        # и ROI остаются прежними — меняется только единица измерения.
        factor = await fx.factor(was, cur)
        cols = (
            "cost_price", "restore_cost", "delivery_cost", "platform_fee",
            "selling_price", "list_price", "price_before_discount",
        )
        res = await session.execute(
            update(Item)
            .where(Item.store_id == member.store_id)
            .values(**{c: func.round(getattr(Item, c) * factor, 2) for c in cols})
        )
        moved = res.rowcount or 0

    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.SETTINGS_EDIT,
        summary=f"базовая валюта {was} → {cur}"
        + (f" · пересчитано вещей: {moved}" if moved else ""),
    )
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
        await session.execute(
            select(User).where(func.lower(User.username) == uname)
        )
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
        audit.record(
            session,
            store_id=member.store_id,
            user_id=user.id,
            action=audit.MEMBER_INVITE,
            summary=f"@{uname} · {payload.role.value} · подключён сразу",
        )
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
    audit.record(
        session,
        store_id=member.store_id,
        user_id=user.id,
        action=audit.MEMBER_INVITE,
        summary=f"@{uname} · {payload.role.value} · ждёт первого /start",
    )
    await session.commit()
    await session.refresh(inv)
    return InviteOut.model_validate(inv)


@router.delete("/members/{user_id}", status_code=204)
async def remove_member(
    user_id: uuid.UUID,
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Исключить сотрудника из склада.

    Раньше такого пути не было вовсе: отозвать можно было только
    приглашение, а уже подключённый человек оставался в складе навсегда.
    Уволенный сотрудник продолжал видеть и править чужие вещи.

    Владельца не исключаем: склад без владельца настроить будет некому.
    Заведённые им вещи и записи журнала остаются — это история склада,
    а не собственность человека.
    """
    if user_id == user.id:
        raise HTTPException(422, "Себя из склада не исключить")

    target = (
        await session.execute(
            select(StoreMember).where(
                StoreMember.user_id == user_id,
                StoreMember.store_id == member.store_id,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "Участник не найден")
    if target.role == Role.OWNER:
        raise HTTPException(422, "Владельца склада исключить нельзя")

    gone = (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()

    # Гасим и приглашение, иначе человек вернётся при следующем /start.
    if gone is not None and gone.username:
        await session.execute(
            update(StoreInvite)
            .where(
                StoreInvite.store_id == member.store_id,
                StoreInvite.username == gone.username.lower(),
                StoreInvite.status.in_((InviteStatus.PENDING, InviteStatus.ACCEPTED)),
            )
            .values(status=InviteStatus.REVOKED)
        )

    # Если исключённый сейчас «стоит» на этом складе — переводим на другой
    # свой, а если других нет, оставляем без активного: приложение покажет
    # понятный экран, а не чужие вещи.
    if gone is not None and gone.current_store_id == member.store_id:
        other = (
            await session.execute(
                select(StoreMember.store_id).where(
                    StoreMember.user_id == user_id,
                    StoreMember.store_id != member.store_id,
                )
            )
        ).scalars().first()
        gone.current_store_id = other

    handle = f"@{gone.username}" if gone is not None and gone.username else "участник"
    audit.record(
        session,
        store_id=member.store_id,
        user_id=user.id,
        action=audit.MEMBER_REMOVE,
        summary=f"{handle} · {target.role.value} · исключён из склада",
    )
    await session.delete(target)
    await session.commit()


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
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.MEMBER_REVOKE,
        summary=f"@{inv.username}",
    )
    await session.commit()
