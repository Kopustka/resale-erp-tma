"""Скидки: расчёт, округление, объявление в канале.

Объявление уходит ОТВЕТОМ на пост вещи, а не отдельным сообщением: так
подписчик видит, к чему относится новая цена, и может сразу открыть
исходную карточку с фото.
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_FLOOR

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Channel, Discount, Item, ItemPost, JobKind, JobStatus, PostJob
from . import post_queue

log = logging.getLogger("discounts")

# Быстрые кнопки в интерфейсе. Держим здесь, чтобы фронт и бэкенд
# считали одинаково и процент в объявлении сходился с нажатой кнопкой.
QUICK_PERCENTS = (10, 20, 30)

# Округляем ВНИЗ до целых. Вниз, а не к ближайшему: при округлении вверх
# покупатель получил бы скидку меньше обещанной, и «−30%» оказалось бы
# неправдой. Единица округления — если понадобятся круглые ценники
# (100, 150), поменять на Decimal("10").
ROUND_TO = Decimal("1")


def apply_percent(price: Decimal, percent: int) -> Decimal:
    """Цена со скидкой в N процентов, округлённая вниз."""
    raw = Decimal(price) * (Decimal(100 - percent) / Decimal(100))
    return round_price(raw)


def round_price(value: Decimal) -> Decimal:
    """Округление вниз до заданного шага."""
    stepped = (Decimal(value) / ROUND_TO).to_integral_value(rounding=ROUND_FLOOR)
    return stepped * ROUND_TO


def percent_of(old: Decimal, new: Decimal) -> int:
    """Фактический процент скидки — считаем по ценам, а не по кнопке."""
    if not old or Decimal(old) <= 0:
        return 0
    return int(round((1 - Decimal(new) / Decimal(old)) * 100))


def _fmt(value: Decimal) -> str:
    f = float(value)
    return str(int(f)) if f == int(f) else f"{f:.2f}".rstrip("0").rstrip(".")


def build_message(discount: Discount, title: str | None, symbol: str) -> str:
    """Текст объявления. Уходит ответом на пост вещи."""
    pct = percent_of(discount.old_price, discount.new_price)
    head = f"🔥 <b>СКИДКА −{pct}%</b>" if pct > 0 else "🔥 <b>НОВАЯ ЦЕНА</b>"
    lines = [head]
    if title:
        from .post_template import esc

        lines.append(esc(title.strip()))
    lines.append(
        f"<s>{_fmt(discount.old_price)} {symbol}</s> → "
        f"<b>{_fmt(discount.new_price)} {symbol}</b>"
    )
    return "\n".join(lines)


async def enqueue_announcements(
    session: AsyncSession, discount: Discount, run_at=None
) -> int:
    """Ставит объявление в каждый канал, где вещь опубликована и не продана."""
    rows = (
        await session.execute(
            select(ItemPost, Channel)
            .join(Channel, Channel.id == ItemPost.channel_id)
            .where(
                ItemPost.item_id == discount.item_id,
                ItemPost.sold_marked.is_(False),
                Channel.enabled.is_(True),
            )
        )
    ).all()
    if not rows:
        return 0

    queued = set(
        (
            await session.execute(
                select(PostJob.channel_uid).where(
                    PostJob.discount_id == discount.id,
                    PostJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                )
            )
        ).scalars().all()
    )

    n = 0
    for post, ch in rows:
        if ch.id in queued:
            continue
        await post_queue.enqueue(
            session,
            store_id=discount.store_id,
            kind=JobKind.DISCOUNT_POST,
            channel_id=ch.chat_id,
            channel_uid=ch.id,
            item_id=discount.item_id,
            discount_id=discount.id,
            # Ответ вешаем на пост вещи в этом канале.
            message_id=post.message_id,
            run_at=run_at,
        )
        n += 1
    if n:
        await session.commit()
    return n
