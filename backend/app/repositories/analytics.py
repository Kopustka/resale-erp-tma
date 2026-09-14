"""Аналитические агрегаты. Возвраты/отмены исключены из прибыли."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import Numeric, and_, case, cast, func, select
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
        """Средние дни от выставления до отправки, по месяцам и категориям."""
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
            .where(ItemStatusLog.new_status == ItemStatus.SHIPPED)
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
                        [ItemStatus.SHIPPED]
                    ),
                )
            )
        ).scalar_one()

    async def stage_times(self, store_id: uuid.UUID) -> list[dict]:
        """Сколько вещь в среднем проводит на каждом этапе.

        Считаем по истории переходов: время на этапе — это промежуток между
        записью, которая в него привела, и следующей записью. У вещи, которая
        стоит на этапе прямо сейчас, следующей записи нет, поэтому берём срок
        до текущего момента: незакрытое ожидание и есть узкое место, а
        отбросив его, мы бы отчитались о скорости, которой нет.

        Откаты назад тоже попадают в среднее — и правильно: вернули вещь на
        доработку, значит она снова заняла время на том этапе.
        """
        nxt = func.lead(ItemStatusLog.created_at).over(
            partition_by=ItemStatusLog.item_id, order_by=ItemStatusLog.created_at
        )
        spans = (
            select(
                ItemStatusLog.new_status.label("stage"),
                ItemStatusLog.item_id.label("item_id"),
                func.coalesce(nxt, func.now()).label("left_at"),
                ItemStatusLog.created_at.label("entered_at"),
                nxt.label("raw_next"),
            )
            .join(Item, Item.id == ItemStatusLog.item_id)
            .where(Item.store_id == store_id, Item.archived_at.is_(None))
            .subquery()
        )
        days = func.extract("epoch", spans.c.left_at - spans.c.entered_at) / 86400.0
        rows = (
            await self.session.execute(
                select(
                    spans.c.stage,
                    func.avg(days).label("avg_days"),
                    func.max(days).label("max_days"),
                    func.count().label("passes"),
                    func.count().filter(spans.c.raw_next.is_(None)).label("now_here"),
                ).group_by(spans.c.stage)
            )
        ).all()
        out = []
        for stage, avg_days, max_days, passes, now_here in rows:
            out.append(
                {
                    "status": stage.value if hasattr(stage, "value") else str(stage),
                    "avg_days": float(avg_days or 0),
                    "max_days": float(max_days or 0),
                    "passes": int(passes or 0),
                    "now_here": int(now_here or 0),
                }
            )
        # Порядок цепочки, а не алфавит: экран читается сверху вниз как путь вещи.
        order = [s.value for s in ItemStatus]
        out.sort(key=lambda r: order.index(r["status"]) if r["status"] in order else 99)
        return out

    async def by_group(self, store_id: uuid.UUID, field: str) -> list[dict]:
        """Что приносит деньги: разрез по бренду или категории.

        Наценку считаем только по проданным: у непроданной вещи прибыли нет,
        и включать её в среднее значит занижать результат тем, что ещё не
        случилось.
        """
        col = Item.brand if field == "brand" else Item.category
        # Пустые значения не выбрасываем, а собираем в отдельную строку.
        # Бренд стал необязательным, и молчаливый пропуск означал бы, что
        # прибыль с безымянных вещей исчезает из разреза «что приносит
        # деньги», а сумма по строкам перестаёт сходиться с общей.
        name_col = func.coalesce(
            func.nullif(func.btrim(col), ""),
            "Без бренда" if field == "brand" else "Без категории",
        )
        invested = Item.cost_price + Item.restore_cost + Item.delivery_cost
        profit = Item.selling_price - invested - Item.platform_fee
        is_sold = and_(Item.status.in_(SOLD_STATUSES), Item.selling_price.isnot(None))
        # От закупки, а не от заведения в систему: вещь могли внести спустя
        # неделю после покупки, и срок «купил → продал» вышел бы короче правды.
        bought_at = func.coalesce(Item.purchase_date, Item.created_at)
        sold_days = func.extract("epoch", Item.sold_date - bought_at) / 86400.0

        rows = (
            await self.session.execute(
                select(
                    name_col.label("name"),
                    func.count().label("total"),
                    func.count().filter(is_sold).label("sold"),
                    func.coalesce(
                        func.sum(case((is_sold, profit), else_=0)), 0
                    ).label("profit"),
                    func.sum(case((is_sold, invested), else_=0)).label("sold_invested"),
                    func.avg(case((is_sold, sold_days))).label("avg_days"),
                    func.coalesce(
                        func.sum(case((~is_sold, invested), else_=0)), 0
                    ).label("frozen"),
                )
                .where(
                    Item.store_id == store_id,
                    Item.archived_at.is_(None),
                )
                .group_by(name_col)
                .order_by(func.count().desc())
                .limit(20)
            )
        ).all()

        out = []
        for name, total, sold, profit_v, sold_invested, avg_days, frozen in rows:
            inv = float(sold_invested or 0)
            out.append(
                {
                    "name": name,
                    "total": int(total or 0),
                    "sold": int(sold or 0),
                    "profit": float(profit_v or 0),
                    # Наценка в процентах к вложенному. Без вложений процент
                    # не определён — отдаём null, а не бесконечность.
                    "markup": (float(profit_v or 0) / inv * 100) if inv > 0 else None,
                    "avg_days": float(avg_days) if avg_days is not None else None,
                    "frozen": float(frozen or 0),
                }
            )
        return out

    async def by_month(self, store_id: uuid.UUID, months: int = 12) -> list[dict]:
        """Прибыль и выручка по месяцам продажи."""
        since = datetime.now(timezone.utc) - timedelta(days=31 * months)
        invested = Item.cost_price + Item.restore_cost + Item.delivery_cost
        profit = Item.selling_price - invested - Item.platform_fee
        rows = (
            await self.session.execute(
                select(
                    func.to_char(Item.sold_date, "YYYY-MM").label("month"),
                    func.count().label("sold"),
                    func.coalesce(func.sum(Item.selling_price), 0).label("revenue"),
                    func.coalesce(func.sum(profit), 0).label("profit"),
                )
                .where(
                    Item.store_id == store_id,
                    Item.status.in_(SOLD_STATUSES),
                    Item.selling_price.isnot(None),
                    Item.sold_date.isnot(None),
                    Item.sold_date >= since,
                )
                .group_by("month")
                .order_by("month")
            )
        ).all()
        return [
            {
                "month": m,
                "sold": int(c or 0),
                "revenue": float(r or 0),
                "profit": float(p or 0),
            }
            for m, c, r, p in rows
        ]
