"""End-to-end смоук: создаёт схему, гоняет полный жизненный цикл товара,
проверяет FSM-переходы, optimistic lock, аналитику и Scout-агрегаты.

Запуск: BOT_TOKEN=123456789:AA... python smoke_test.py
Требует поднятый PostgreSQL (DATABASE_URL) — таблицы создаются автоматически.
"""

# --------------------------------------------------------------------------
# ВНИМАНИЕ: этот скрипт делает DROP SCHEMA public CASCADE — он стирает БД
# целиком. Один запуск по DATABASE_URL продакшена уничтожает весь склад.
# Поэтому по умолчанию он не стартует: нужен явный ALLOW_DESTRUCTIVE_TESTS=1.
# Запускать только на одноразовой базе, никогда — на боевой.
# --------------------------------------------------------------------------
import os as _os
import sys as _sys

if _os.environ.get("ALLOW_DESTRUCTIVE_TESTS") != "1":
    _sys.exit(
        "ОТКАЗ: скрипт стирает базу целиком (DROP SCHEMA).\n"
        "Запуск только на одноразовой БД: ALLOW_DESTRUCTIVE_TESTS=1 "
        "DATABASE_URL=<тестовая> python " + _os.path.basename(__file__)
    )

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db import Base, SessionLocal, engine
from app.models import (
    Item,
    ItemStatus,
    ItemStatusLog,
    Role,
    Store,
    StoreMember,
    User,
)
from app.repositories.analytics import AnalyticsRepository
from app.repositories.items import ItemRepository


async def reset_schema():
    from sqlalchemy import text

    async with engine.begin() as conn:
        # чистый сброс (обходит циклический FK с use_alter)
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)


async def main():
    await reset_schema()
    ok = []

    async with SessionLocal() as s:
        # --- склад + владелец ---
        user = User(telegram_id=111, username="owner", first_name="Boss")
        s.add(user)
        await s.flush()
        store = Store(name="Склад 1", owner_id=user.id)
        s.add(store)
        await s.flush()

        # Плоские id — устойчивы к expire после rollback/commit
        store_id = store.id
        user_id = user.id

        s.add(StoreMember(user_id=user_id, store_id=store_id, role=Role.OWNER))
        user.current_store_id = store_id
        await s.commit()

        repo = ItemRepository(s)

        # --- SKU без гонок ---
        sku1 = await repo.next_sku(store_id)
        sku2 = await repo.next_sku(store_id)
        assert sku1 == "#1001" and sku2 == "#1002", (sku1, sku2)
        ok.append(f"SKU monotonic: {sku1}, {sku2}")

        # --- создаём товар ---
        item = Item(
            store_id=store_id, sku="#1001", title="Худи Nike", brand="nike",
            category="худи", cost_price=Decimal("40"), restore_cost=Decimal("5"),
            delivery_cost=Decimal("5"), purchase_location="Секонд А",
            purchaser_id=user.id,
        )
        s.add(item)
        await s.commit()
        await s.refresh(item)
        item_id = item.id
        ok.append(f"Item created, status={item.status.value}, version={item.version}")

        # --- FSM: happy path до SOLD c проставлением дат ---
        for target in [ItemStatus.PREPARING, ItemStatus.PHOTOGRAPHED, ItemStatus.LISTED]:
            fresh = await repo.get(store_id, item_id)
            done = await repo.apply_status(fresh, target, fresh.version, user_id)
            assert done, target
            await s.commit()
        listed = await repo.get(store_id, item_id)
        assert listed.status == ItemStatus.LISTED and listed.listed_date is not None
        ok.append(f"Reached LISTED, listed_date set, version={listed.version}")

        # --- optimistic lock: старая версия отклоняется ---
        stale_version = listed.version - 1
        conflict = await repo.apply_status(
            listed, ItemStatus.BOOKED, stale_version, user_id
        )
        assert conflict is False, "stale version must fail"
        await s.rollback()
        ok.append("Optimistic lock rejected stale version (409 path)")

        # --- продажа с ценой ---
        cur = await repo.get(store_id, item_id)
        await repo.apply_status(cur, ItemStatus.BOOKED, cur.version, user_id)
        await s.commit()
        cur = await repo.get(store_id, item_id)
        await repo.apply_status(
            cur, ItemStatus.SOLD, cur.version, user_id, selling_price=Decimal("120")
        )
        await s.commit()
        sold = await repo.get(store_id, item_id)
        assert sold.status == ItemStatus.SOLD and sold.sold_date is not None
        assert sold.selling_price == Decimal("120")
        # net_profit = 120 - (40+5+5) - 0 = 70 ; roi = 70/50*100 = 140
        assert sold.net_profit == Decimal("70"), sold.net_profit
        assert sold.roi_percent == Decimal("140"), sold.roi_percent
        ok.append(f"SOLD: profit={sold.net_profit}, ROI={sold.roi_percent}%")

        # подделаем listed_date в прошлое, чтобы turnover/Scout дали дни
        # (правим лог LISTED на 12 дней назад)
        log = (
            await s.execute(
                __import__("sqlalchemy").select(ItemStatusLog).where(
                    ItemStatusLog.item_id == item_id,
                    ItemStatusLog.new_status == ItemStatus.LISTED,
                )
            )
        ).scalar_one()
        log.created_at = datetime.now(timezone.utc) - timedelta(days=12)
        await s.commit()

        # --- аналитика ---
        an = AnalyticsRepository(s)
        total = await an.total_profit(store_id)
        assert total == Decimal("70"), total
        loc = await an.roi_by_location(store_id)
        assert loc and loc[0]["location"] == "Секонд А"
        scout = await an.scout_lookup(store_id, "nike", "худи")
        assert scout["found_count"] == 1, scout
        assert scout["avg_days_to_sell"] is not None and scout["avg_days_to_sell"] >= 11
        ok.append(
            f"Analytics: total_profit={total}, location_roi={loc[0]['roi_percent']}, "
            f"scout_days={scout['avg_days_to_sell']}, scout_profit={scout['avg_profit']}"
        )

        # --- FSM запрет недопустимого перехода уже проверяет роутер; проверим матрицу ---
        from app.services.fsm import can_transition
        assert can_transition(ItemStatus.SOLD, ItemStatus.RETURNED)
        assert not can_transition(ItemStatus.BOUGHT, ItemStatus.SOLD)
        ok.append("FSM matrix: SOLD->RETURNED ok, BOUGHT->SOLD blocked")

    print("\n".join(f"  ✓ {x}" for x in ok))
    print("\nSMOKE TEST PASSED ✅")


if __name__ == "__main__":
    asyncio.run(main())
