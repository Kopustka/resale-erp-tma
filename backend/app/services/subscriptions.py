"""Подписки покупателей: «сообщи, когда появится подходящее».

Пустой фильтр значит «любой»: подписка только на бренд ловит все размеры
этого бренда. Сравнение регистронезависимое — покупатель напишет «nike»,
а в карточке будет «Nike».

Рассылка идёт через общую очередь публикаций: там уже есть ретраи и пауза
между отправками, а личные сообщения — как раз то место, где превышение
частоты приводит к блокировке бота.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Item, JobKind, JobStatus, PostJob, Store, Subscription
from . import post_queue

log = logging.getLogger("subs")

# Не чаще одного уведомления в час одному подписчику: даже при массовом
# выставлении человек не должен получить десяток сообщений подряд.
NOTIFY_COOLDOWN_MINUTES = 60
MAX_PER_ITEM = 50


def _matches_sql(item: Item):
    """Условия совпадения подписки с вещью (NULL в фильтре = «любой»)."""
    def eq(col, value):
        v = (value or "").strip().lower()
        return or_(col.is_(None), func.lower(col) == v) if v else col.is_(None)

    conds = [eq(Subscription.brand, item.brand), eq(Subscription.category, item.category)]
    size = (item.size or "").strip().lower()
    if size:
        conds.append(or_(Subscription.size.is_(None), func.lower(Subscription.size) == size))
    else:
        conds.append(Subscription.size.is_(None))

    price = item.list_price_orig if item.list_price_orig is not None else item.selling_price_orig
    if price is not None:
        conds.append(or_(Subscription.max_price.is_(None), Subscription.max_price >= price))
    return conds


async def enqueue_notifications(
    session: AsyncSession, store_id: uuid.UUID, item: Item
) -> int:
    """Ставит уведомления подписчикам, которым подходит вещь."""
    store = (
        await session.execute(select(Store).where(Store.id == store_id))
    ).scalar_one_or_none()
    if store is None or not store.subscriptions_enabled:
        return 0

    cooldown = datetime.now(timezone.utc) - timedelta(minutes=NOTIFY_COOLDOWN_MINUTES)
    subs = (
        await session.execute(
            select(Subscription)
            .where(
                Subscription.store_id == store_id,
                Subscription.active.is_(True),
                or_(
                    Subscription.last_notified_at.is_(None),
                    Subscription.last_notified_at <= cooldown,
                ),
                *_matches_sql(item),
            )
            .limit(MAX_PER_ITEM)
        )
    ).scalars().all()
    if not subs:
        return 0

    already = set(
        (
            await session.execute(
                select(PostJob.sub_id).where(
                    PostJob.item_id == item.id,
                    PostJob.kind == JobKind.NOTIFY_SUB,
                    PostJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                )
            )
        ).scalars().all()
    )

    queued = 0
    now = datetime.now(timezone.utc)
    for sub in subs:
        if sub.id in already:
            continue
        await post_queue.enqueue(
            session,
            store_id=store_id,
            kind=JobKind.NOTIFY_SUB,
            channel_id=str(sub.telegram_id),  # для DM «канал» — это чат человека
            item_id=item.id,
            sub_id=sub.id,
        )
        sub.last_notified_at = now
        queued += 1
    if queued:
        await session.commit()
        log.info("подписчиков уведомим: %s (вещь %s)", queued, item.sku)
    return queued


def describe(sub: Subscription) -> str:
    """Человекочитаемое описание подписки для списка в боте."""
    parts = [p for p in (sub.brand, sub.category, sub.size) if p]
    text = " · ".join(parts) if parts else "любые новинки"
    if sub.max_price is not None:
        text += f" · до {sub.max_price:g}"
    return text
