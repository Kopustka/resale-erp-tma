"""Аналитические агрегаты. Возвраты/отмены исключены из прибыли."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Item, ItemStatus, ItemStatusLog
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
