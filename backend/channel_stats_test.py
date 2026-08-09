"""Неразрушающий тест аналитики по каналам."""
import asyncio, sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import delete, select
from app.db import SessionLocal
from app.models import (Channel, Item, ItemPost, ItemStatus, Role, Store,
                        StoreCounter, StoreMember, User)
from app.repositories.analytics import AnalyticsRepository

TG=999789; ok=bad=0
NOW=datetime.now(timezone.utc)
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)

async def main():
    global ok,bad
    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="cs",first_name="CS"); s.add(u); await s.flush()
        st=Store(name="STATS-TEST",owner_id=u.id); s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); s.add(StoreCounter(store_id=st.id))
        u.current_store_id=st.id
        a=Channel(store_id=st.id,chat_id="@a",title="Первый"); s.add(a)
        b=Channel(store_id=st.id,chat_id="@b",title="Второй"); s.add(b)
        c=Channel(store_id=st.id,chat_id="@c",title="Пустой"); s.add(c)
        await s.flush()

        def mk(sku, sold, price=None, days=None, archived=False):
            it=Item(store_id=st.id,sku=sku,title="T",brand="N",category="C",
                    status=ItemStatus.SOLD if sold else ItemStatus.LISTED,
                    cost_price=Decimal("50"), restore_cost=Decimal(0), delivery_cost=Decimal(0),
                    platform_fee=Decimal(0),
                    selling_price=Decimal(price) if price else None,
                    sold_date=(NOW if sold else None),
                    archived_at=NOW if archived else None)
            s.add(it); return it

        i1=mk("#S1",True,"200")     # продан, в канале A, 10 дней
        i2=mk("#S2",False)          # висит, в канале A
        i3=mk("#S3",True,"150")     # продан, в A и B одновременно
        i4=mk("#S4",True,"999",archived=True)  # архив — не считаем
        await s.flush()
        s.add(ItemPost(item_id=i1.id,channel_id=a.id,message_id=1,created_at=NOW-timedelta(days=10)))
        s.add(ItemPost(item_id=i2.id,channel_id=a.id,message_id=2))
        s.add(ItemPost(item_id=i3.id,channel_id=a.id,message_id=3,created_at=NOW-timedelta(days=4)))
        s.add(ItemPost(item_id=i3.id,channel_id=b.id,message_id=4,created_at=NOW-timedelta(days=4)))
        s.add(ItemPost(item_id=i4.id,channel_id=b.id,message_id=5))
        await s.commit(); sid=st.id
    try:
        async with SessionLocal() as s:
            rows = await AnalyticsRepository(s).by_channel(sid)
        by = {r["chat_id"]: r for r in rows}
        print("\n[1] Состав ответа")
        chk(len(rows)==3, "все каналы, включая пустой", str(len(rows)))
        chk(by["@c"]["posted"]==0 and by["@c"]["sold"]==0, "пустой канал с нулями")
        chk(by["@c"]["sell_through"] is None, "конверсия у пустого — не ноль, а None")

        print("\n[2] Подсчёты по каналу A")
        chk(by["@a"]["posted"]==3, "выложено 3", str(by["@a"]["posted"]))
        chk(by["@a"]["sold"]==2, "продано 2", str(by["@a"]["sold"]))
        chk(abs(by["@a"]["sell_through"]-66.66)<1, "конверсия ~67%", str(by["@a"]["sell_through"]))
        chk(by["@a"]["profit"]==Decimal("250"), "прибыль 150+100=250", str(by["@a"]["profit"]))
        chk(by["@a"]["avg_days"] is not None and 6 < by["@a"]["avg_days"] < 8,
            "средний срок ~7 дней", str(by["@a"]["avg_days"]))

        print("\n[3] Архив исключён")
        chk(by["@b"]["posted"]==1, "в B засчитан только неархивный пост", str(by["@b"]["posted"]))

        print("\n[4] Вещь в двух каналах")
        chk(by["@b"]["sold"]==1 and by["@a"]["sold"]==2,
            "продажа засчитана обоим каналам (заявленное поведение)",
            f"A={by['@a']['sold']} B={by['@b']['sold']}")
    finally:
        async with SessionLocal() as s:
            ids=(await s.execute(select(Item.id).where(Item.store_id==sid))).scalars().all()
            if ids: await s.execute(delete(ItemPost).where(ItemPost.item_id.in_(ids)))
            await s.execute(delete(Channel).where(Channel.store_id==sid))
            await s.execute(delete(Item).where(Item.store_id==sid))
            await s.execute(delete(StoreCounter).where(StoreCounter.store_id==sid))
            await s.execute(delete(StoreMember).where(StoreMember.store_id==sid))
            u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one()
            u.current_store_id=None; await s.flush()
            await s.execute(delete(Store).where(Store.id==sid))
            await s.execute(delete(User).where(User.id==u.id)); await s.commit()
        print("\n[cleanup] ok")
    print(f"\nИТОГ: {ok} успешно, {bad} провалено")
    sys.exit(1 if bad else 0)

asyncio.run(main())
