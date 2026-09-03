"""Скидки на вещь: сразу или по расписанию, с объявлением в канале."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CAN_EDIT, get_active_membership, get_current_user
from ..db import get_session
from ..models import (
    Discount,
    Store,
    ItemStatus,
    DiscountStatus,
    Item,
    JobStatus,
    PostJob,
    StoreMember,
    User,
)
from ..schemas import DiscountCreate, DiscountOut
from ..services import discounts as svc
from ..services import audit, preview, preview_hold
from ..services import fx

router = APIRouter(prefix="/api/v1/discounts", tags=["discounts"])

MAX_AHEAD_DAYS = 365


def _out(d: Discount, sku: str | None = None, title: str | None = None) -> DiscountOut:
    return DiscountOut(
        id=d.id,
        item_id=d.item_id,
        item_sku=sku,
        item_title=title,
        old_price=d.old_price,
        new_price=d.new_price,
        percent=svc.percent_of(d.old_price, d.new_price),
        currency=d.currency,
        scheduled_at=d.scheduled_at,
        status=d.status.value,
        created_at=d.created_at,
    )


@router.get("", response_model=list[DiscountOut])
async def list_discounts(
    item_id: uuid.UUID | None = Query(None, description="только по одной вещи"),
    include_done: bool = Query(False),
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    q = (
        select(Discount, Item.sku, Item.title)
        .join(Item, Item.id == Discount.item_id)
        .where(Discount.store_id == member.store_id)
    )
    if item_id is not None:
        q = q.where(Discount.item_id == item_id)
    if not include_done:
        q = q.where(Discount.status == DiscountStatus.SCHEDULED)
    rows = (await session.execute(q.order_by(Discount.created_at.desc()))).all()
    return [_out(d, sku, title) for d, sku, title in rows]


@router.post("", response_model=DiscountOut, status_code=201)
async def create_discount(
    payload: DiscountCreate,
    user: User = Depends(get_current_user),
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    """Ставит скидку. Без даты — объявляем сразу."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot set discounts")

    item = (
        await session.execute(
            select(Item).where(
                Item.id == payload.item_id, Item.store_id == member.store_id
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "Вещь не найдена")

    # Объявление уходит ОТВЕТОМ на пост вещи. Пока вещь не выложена,
    # поста нет — отвечать не на что, и скидка была бы объявлением
    # в пустоту.
    if item.status != ItemStatus.LISTED:
        raise HTTPException(
            422, "Скидку можно сделать только на выложенную вещь"
        )

    old = item.list_price_orig
    if old is None:
        raise HTTPException(422, "У вещи нет цены — сначала укажите её")

    currency = item.price_currency or "BYN"
    new = svc.round_price(Decimal(str(payload.new_price)), currency)
    if new <= 0:
        raise HTTPException(422, "Цена со скидкой должна быть больше нуля")
    if new >= Decimal(old):
        # Скидка, поднимающая цену, — это не скидка. Ловим до записи,
        # иначе в канал уйдёт «−0%» и подписчики решат, что их дурят.
        raise HTTPException(
            422,
            f"Цена со скидкой должна быть меньше текущей ({svc._fmt(Decimal(old))})",
        )

    when = payload.scheduled_at
    if when is not None:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if (now - when).total_seconds() > 300:
            raise HTTPException(422, "Время публикации в прошлом")
        if (when - now).days > MAX_AHEAD_DAYS:
            raise HTTPException(422, "Слишком далеко в будущем")

    discount = Discount(
        store_id=member.store_id,
        item_id=item.id,
        old_price=Decimal(old),
        new_price=new,
        currency=currency,
        scheduled_at=when,
        created_by=user.id,
    )
    session.add(discount)
    await session.flush()

    # Немедленную скидку сразу применяем к вещи: цена в карточке и в подписи
    # поста должна совпасть с той, что объявлена ответом. Отложенную трогаем
    # только в момент публикации — до тех пор вещь продаётся по старой цене.
    if when is None:
        base = (
            await session.execute(
                select(Item.cost_currency).where(Item.id == item.id)
            )
        ).scalar_one_or_none()
        item.price_before_discount = Decimal(old)
        item.list_price_orig = new
        item.list_price = await fx.convert(new, currency, (base or "BYN"))
    audit.record(
        session,
        store_id=member.store_id,
        user_id=user.id,
        action=audit.DISCOUNT_CREATE,
        summary=f"{item.sku or ''} {item.title or ''}".strip()
        + f" · {svc._fmt(Decimal(old))} → {svc._fmt(new)} {currency}"
        + ("" if when is None else " (по расписанию)"),
        entity_type="discount",
        entity_id=discount.id,
    )
    await session.commit()
    await session.refresh(discount)

    hold = await preview.is_enabled(session, member.store_id)
    jobs = await svc.enqueue_announcements(session, discount, run_at=when, hold=hold)

    if hold and jobs:
        store = (
            await session.execute(select(Store).where(Store.id == member.store_id))
        ).scalar_one_or_none()
        preview_hold.send_in_background(
            telegram_id=user.telegram_id,
            kind=preview.DISCOUNT,
            entity_id=discount.id,
            body=svc.build_message(
                discount,
                item,
                fx.symbol(currency),
                store.discount_template if store else None,
                store.channel_signature if store else None,
            ),
            photo_entry=None,
            channels=len(jobs),
            job_ids=[j.id for j in jobs],
            when=when,
        )
    return _out(discount, item.sku, item.title)


@router.delete("/{discount_id}", status_code=204)
async def cancel_discount(
    discount_id: uuid.UUID,
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    """Отменяет запланированную скидку и снимает её задания."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot set discounts")
    d = (
        await session.execute(
            select(Discount).where(
                Discount.id == discount_id, Discount.store_id == member.store_id
            )
        )
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "Скидка не найдена")
    if d.status == DiscountStatus.PUBLISHED:
        raise HTTPException(409, "Скидка уже объявлена — отменить нельзя")

    d.status = DiscountStatus.CANCELLED
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.DISCOUNT_CANCEL,
        summary=f"{svc._fmt(d.old_price)} → {svc._fmt(d.new_price)} {d.currency}",
        entity_type="discount",
        entity_id=d.id,
    )
    await session.execute(
        update(PostJob)
        .where(
            PostJob.discount_id == d.id,
            PostJob.status.in_((JobStatus.PENDING, JobStatus.AWAITING)),
        )
        .values(status=JobStatus.CANCELLED)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
