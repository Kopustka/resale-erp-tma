"""Репозиторий товаров: скоуп по складу + soft-delete + optimistic lock."""
from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Item, ItemStatus, ItemStatusLog, StoreCounter
from ..services.fsm import PRE_LISTED, PRE_SOLD


def _encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{item_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    ts, iid = raw.split("|")
    return datetime.fromisoformat(ts), uuid.UUID(iid)


class ItemRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def next_sku(self, store_id: uuid.UUID) -> str:
        """Монотонный SKU без гонок (row lock на счётчике склада)."""
        counter = (
            await self.session.execute(
                select(StoreCounter)
                .where(StoreCounter.store_id == store_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if counter is None:
            counter = StoreCounter(store_id=store_id, last_sku=1000)
            self.session.add(counter)
            await self.session.flush()
        counter.last_sku += 1
        return f"#{counter.last_sku}"

    async def list_page(
        self,
        store_id: uuid.UUID,
        *,
        limit: int = 30,
        cursor: str | None = None,
        status: ItemStatus | None = None,
        brand: str | None = None,
        search: str | None = None,
        stale_before: datetime | None = None,
        ids: list[uuid.UUID] | None = None,
        archived: bool = False,
    ) -> tuple[list[Item], str | None]:
        conds = [Item.store_id == store_id]
        conds.append(
            Item.archived_at.isnot(None) if archived else Item.archived_at.is_(None)
        )
        if status is not None:
            conds.append(Item.status == status)
        if brand:
            conds.append(Item.brand.ilike(brand))
        if search:
            like = f"%{search}%"
            conds.append(
                Item.brand.ilike(like)
                | Item.sku.ilike(like)
                | Item.title.ilike(like)
            )
        if stale_before is not None:
            conds.append(Item.listed_date.isnot(None))
            conds.append(Item.listed_date < stale_before)
            conds.append(Item.status == ItemStatus.LISTED)
        if ids is not None:
            conds.append(Item.id.in_(ids))
        if cursor:
            c_ts, c_id = _decode_cursor(cursor)
            conds.append(
                (Item.created_at < c_ts)
                | and_(Item.created_at == c_ts, Item.id < c_id)
            )

        rows = (
            await self.session.execute(
                select(Item)
                .where(*conds)
                .order_by(Item.created_at.desc(), Item.id.desc())
                .limit(limit + 1)
            )
        ).scalars().all()

        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = _encode_cursor(last.created_at, last.id)
            rows = rows[:limit]
        return rows, next_cursor

    async def get(
        self, store_id: uuid.UUID, item_id: uuid.UUID, *, include_archived: bool = False
    ) -> Item | None:
        conds = [Item.id == item_id, Item.store_id == store_id]
        if not include_archived:
            conds.append(Item.archived_at.is_(None))
        return (
            await self.session.execute(select(Item).where(*conds))
        ).scalar_one_or_none()

    async def apply_status(
        self,
        item: Item,
        new_status: ItemStatus,
        expected_version: int,
        changed_by: uuid.UUID,
        selling_price=None,      # база (в валюте склада)
        selling_orig=None,       # оригинал (в валюте продажи)
        sell_currency=None,      # валюта продажи
    ) -> bool:
        """Атомарный переход статуса с optimistic lock. True=успех, False=конфликт версий."""
        now = datetime.now(timezone.utc)
        # Снимаем нужные атрибуты ДО update — после него они истекают (async lazy-load запрещён).
        old_status = item.status
        item_id = item.id
        listed_is_empty = item.listed_date is None

        values: dict = {
            "status": new_status,
            "version": Item.version + 1,
            "updated_at": now,
        }
        if new_status == ItemStatus.LISTED and listed_is_empty:
            values["listed_date"] = now
        if new_status == ItemStatus.SOLD:
            values["sold_date"] = now
            if selling_price is not None:
                values["selling_price"] = selling_price
                values["selling_price_orig"] = selling_orig
                if sell_currency:
                    values["price_currency"] = sell_currency
        # Откат назад: до продажи — сбрасываем sold_date (вещь не продана),
        # до листинга — сбрасываем и listed_date (счётчик дней пойдёт заново).
        if new_status in PRE_SOLD:
            values["sold_date"] = None
        if new_status in PRE_LISTED:
            values["listed_date"] = None

        res = await self.session.execute(
            update(Item)
            .where(Item.id == item_id, Item.version == expected_version)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount == 0:
            return False

        self.session.add(
            ItemStatusLog(
                item_id=item_id,
                old_status=old_status,
                new_status=new_status,
                changed_by=changed_by,
            )
        )
        # UPDATE прошёл мимо identity-map (synchronize_session=False) — помечаем
        # объект как expired, чтобы следующее чтение подтянуло свежие version/status.
        self.session.expire(item)
        return True

    async def archive(self, item: Item) -> None:
        item.archived_at = datetime.now(timezone.utc)
        item.version += 1

    async def restore(self, item: Item) -> None:
        item.archived_at = None
        item.version += 1

    async def update_fields(self, item: Item, data: dict) -> None:
        """Обновляет переданные (не-None) поля товара, бампает версию."""
        for key, value in data.items():
            setattr(item, key, value)
        item.version += 1
        item.updated_at = datetime.now(timezone.utc)

    async def hard_delete(self, item: Item) -> None:
        """Безвозвратное удаление: сначала аудит-лог (FK), затем сам товар."""
        item_id = item.id
        await self.session.execute(
            delete(ItemStatusLog).where(ItemStatusLog.item_id == item_id)
        )
        await self.session.execute(delete(Item).where(Item.id == item_id))

    async def suggest(self, store_id: uuid.UUID, field: str, q: str) -> list[str]:
        col = {"brand": Item.brand, "category": Item.category}.get(field, Item.brand)
        rows = (
            await self.session.execute(
                select(col)
                .where(Item.store_id == store_id, col.ilike(f"{q}%"))
                .distinct()
                .limit(10)
            )
        ).scalars().all()
        return list(rows)
