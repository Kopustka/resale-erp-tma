"""Аналитические агрегаты. Возвраты/отмены исключены из прибыли."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import Numeric, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Channel, Item, ItemPost, ItemStatus, ItemStatusLog
from ..services.fsm import SOLD_STATUSES


class AnalyticsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def stale_items(
        self, store_id: uuid.UUID, days: int
    ) -> tuple[int, list[uuid.UUID]]:
        """Товары в LISTED дольше N дней (зависшие)."""
        threshold = datetime.now(timezone.utc) - timedelta(days=days)
        rows = (
            await self.session.execute(
                select(Item.id).where(
                    Item.store_id == store_id,
                    Item.archived_at.is_(None),
                    Item.status == ItemStatus.LISTED,
                    Item.listed_date.isnot(None),
                    Item.listed_date < threshold,
                )
            )
        ).scalars().all()
        return len(rows), list(rows)

    async def by_channel(self, store_id: uuid.UUID) -> list[dict]:
        """Эффективность каналов: выложено, продано, конверсия, срок, прибыль.

        Честная оговорка о цифрах: вещь может висеть сразу в нескольких
        каналах, и по данным нельзя определить, какой из них привёл
        покупателя. Поэтому продажа засчитывается КАЖДОМУ каналу, где вещь
        публиковалась, и сумма прибыли по каналам может превышать общую.
        Это метрика для сравнения каналов между собой, а не разбиение выручки.
        """
        invested = Item.cost_price + Item.restore_cost + Item.delivery_cost
        profit = Item.selling_price - invested - Item.platform_fee
        is_sold = and_(Item.status.in_(SOLD_STATUSES), Item.selling_price.isnot(None))
        days = func.extract("epoch", Item.sold_date - ItemPost.created_at) / 86400.0

        rows = (
            await self.session.execute(
                select(
                    Channel.id.label("channel_id"),
                    Channel.chat_id,
                    Channel.title,
                    Channel.enabled,
                    # Считаем по Item, а не по ItemPost: фильтр архива стоит
                    # в условии соединения, и у архивной вещи Item уходит в NULL,
                    # тогда как строка поста осталась бы посчитанной.
                    func.count(Item.id).label("posted"),
                    func.count(case((is_sold, ItemPost.id))).label("sold"),
                    func.coalesce(
                        func.sum(case((is_sold, profit), else_=0)), 0
                    ).label("profit"),
                    func.avg(
                        case((and_(is_sold, Item.sold_date.isnot(None)), days))
                    ).label("avg_days"),
                    func.coalesce(func.sum(ItemPost.reactions), 0).label("reactions"),
                )
                .select_from(Channel)
                .outerjoin(ItemPost, ItemPost.channel_id == Channel.id)
                .outerjoin(
                    Item,
                    and_(Item.id == ItemPost.item_id, Item.archived_at.is_(None)),
                )
                .where(Channel.store_id == store_id)
                .group_by(Channel.id, Channel.chat_id, Channel.title, Channel.enabled,
                          Channel.created_at)
                .order_by(Channel.created_at)
            )
        ).all()

        out = []
        for r in rows:
            posted = r.posted or 0
            sold = r.sold or 0
            out.append(
                {
                    "channel_id": r.channel_id,
                    "chat_id": r.chat_id,
                    "title": r.title,
                    "enabled": r.enabled,
                    "posted": posted,
                    "sold": sold,
                    "sell_through": (sold / posted * 100) if posted else None,
                    "avg_days": float(r.avg_days) if r.avg_days is not None else None,
                    "profit": r.profit or Decimal(0),
                    "reactions": r.reactions or 0,
                }
            )
        return out

    async def roi_by_location(self, store_id: uuid.UUID) -> list[dict]:
        invested = Item.cost_price + Item.restore_cost + Item.delivery_cost
        profit = Item.selling_price - invested - Item.platform_fee
        rows = (
            await self.session.execute(
                select(
                    func.coalesce(Item.purchase_location, "—").label("location"),
                    func.sum(invested).label("invested"),
                    func.sum(profit).label("profit"),
                )
                .where(
                    Item.store_id == store_id,
                    Item.archived_at.is_(None),
                    Item.status.in_(SOLD_STATUSES),
                    Item.selling_price.isnot(None),
                )
                .group_by("location")
                .order_by(func.sum(profit).desc())
            )
        ).all()
        out = []
        for r in rows:
            inv = r.invested or Decimal(0)
            pr = r.profit or Decimal(0)
            roi = (pr / inv * Decimal(100)) if inv else None
            out.append(
                {"location": r.location, "invested": inv, "profit": pr, "roi_percent": roi}
            )
        return out

    async def turnover(self, store_id: uuid.UUID) -> list[dict]:
        """Средние дни LISTED->SOLD по месяцам и категориям (из аудит-логов)."""
        listed = (
            select(
                ItemStatusLog.item_id,
                func.min(ItemStatusLog.created_at).label("listed_at"),
            )
            .where(ItemStatusLog.new_status == ItemStatus.LISTED)
            .group_by(ItemStatusLog.item_id)
            .subquery()
        )
        sold = (
            select(
                ItemStatusLog.item_id,
                func.min(ItemStatusLog.created_at).label("sold_at"),
            )
            .where(ItemStatusLog.new_status == ItemStatus.SOLD)
            .group_by(ItemStatusLog.item_id)
            .subquery()
        )
        days = func.extract("epoch", sold.c.sold_at - listed.c.listed_at) / 86400.0
        rows = (
            await self.session.execute(
                select(
                    func.to_char(sold.c.sold_at, "YYYY-MM").label("period"),
                    Item.category,
                    func.avg(days).label("avg_days"),
                    func.count().label("sold_count"),
                )
                .join(sold, sold.c.item_id == Item.id)
                .join(listed, listed.c.item_id == Item.id)
                .where(Item.store_id == store_id)
                .group_by("period", Item.category)
                .order_by("period")
            )
        ).all()
        return [
            {
                "period": r.period,
                "category": r.category,
                "avg_days": round(float(r.avg_days or 0), 1),
                "sold_count": r.sold_count,
            }
            for r in rows
        ]

    async def total_profit(self, store_id: uuid.UUID) -> Decimal:
        profit = (
            Item.selling_price
            - Item.cost_price
            - Item.restore_cost
            - Item.delivery_cost
            - Item.platform_fee
        )
        val = (
            await self.session.execute(
                select(func.coalesce(func.sum(profit), 0)).where(
                    Item.store_id == store_id,
                    Item.archived_at.is_(None),
                    Item.status.in_(SOLD_STATUSES),
                    Item.selling_price.isnot(None),
                )
            )
        ).scalar_one()
        return Decimal(val)

    async def active_count(self, store_id: uuid.UUID) -> int:
        return (
            await self.session.execute(
                select(func.count()).where(
                    Item.store_id == store_id,
                    Item.archived_at.is_(None),
                    Item.status.notin_(
                        [ItemStatus.COMPLETED, ItemStatus.CANCELLED, ItemStatus.RETURNED]
                    ),
                )
            )
        ).scalar_one()
