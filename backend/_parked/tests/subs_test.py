"""Неразрушающий тест подписок покупателей."""
import asyncio, sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import delete, select
from app.db import SessionLocal
from app.models import (Item, ItemStatus, JobKind, PostJob, Role, Store,
                        StoreCounter, StoreMember, Subscription, User)
from app.services import subscriptions as S

TG=999790; ok=bad=0; NOW=datetime.now(timezone.utc)
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)

async def clear_jobs(sid):
    async with SessionLocal() as s:
        await s.execute(delete(PostJob).where(PostJob.store_id==sid)); await s.commit()

async def main():
    global ok,bad
    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="sb",first_name="SB"); s.add(u); await s.flush()
        st=Store(name="SUBS-TEST",owner_id=u.id,subscriptions_enabled=True); s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); s.add(StoreCounter(store_id=st.id))
        u.current_store_id=st.id
        it=Item(store_id=st.id,sku="#U1",title="T",brand="Nike",category="Худи",size="L",
                status=ItemStatus.LISTED,list_price_orig=Decimal("150"))
        s.add(it)
        # подписки разной формы
        s.add(Subscription(store_id=st.id,telegram_id=1,brand="nike",size="l"))       # точное, другой регистр
        s.add(Subscription(store_id=st.id,telegram_id=2,brand="Nike"))                # только бренд
        s.add(Subscription(store_id=st.id,telegram_id=3))                             # на всё
        s.add(Subscription(store_id=st.id,telegram_id=4,brand="Adidas"))              # чужой бренд
        s.add(Subscription(store_id=st.id,telegram_id=5,brand="Nike",size="XL"))      # другой размер
        s.add(Subscription(store_id=st.id,telegram_id=6,max_price=Decimal("100")))    # дешевле, чем вещь
        s.add(Subscription(store_id=st.id,telegram_id=7,max_price=Decimal("200")))    # потолок выше
        s.add(Subscription(store_id=st.id,telegram_id=8,brand="Nike",active=False))   # отписался
        s.add(Subscription(store_id=st.id,telegram_id=9,brand="Nike",
                           last_notified_at=NOW-timedelta(minutes=5)))                # в кулдауне
        await s.commit(); sid, iid = st.id, it.id
    try:
        print("\n[1] Подбор подписчиков")
        async with SessionLocal() as s:
            item=(await s.execute(select(Item).where(Item.id==iid))).scalar_one()
            n=await S.enqueue_notifications(s, sid, item)
        async with SessionLocal() as s:
            jobs=(await s.execute(select(PostJob).where(PostJob.kind==JobKind.NOTIFY_SUB))).scalars().all()
        got=sorted(int(j.channel_id) for j in jobs)
        chk(got==[1,2,3,7], "уведомлены ровно подходящие", f"получили {got}")
        chk(n==4, "счётчик совпадает", str(n))

        print("\n[2] Кулдаун и повтор")
        await clear_jobs(sid)
        async with SessionLocal() as s:
            item=(await s.execute(select(Item).where(Item.id==iid))).scalar_one()
            n2=await S.enqueue_notifications(s, sid, item)
        chk(n2==0, "сразу после рассылки — никого (кулдаун час)", str(n2))

        print("\n[3] Выключенные подписки у склада")
        await clear_jobs(sid)
        async with SessionLocal() as s:
            stt=(await s.execute(select(Store).where(Store.id==sid))).scalar_one()
            stt.subscriptions_enabled=False
            await s.execute(delete(Subscription).where(Subscription.store_id==sid))
            s.add(Subscription(store_id=sid,telegram_id=11,brand="Nike"))
            await s.commit()
            item=(await s.execute(select(Item).where(Item.id==iid))).scalar_one()
            n3=await S.enqueue_notifications(s, sid, item)
        chk(n3==0, "при выключенном тумблере рассылки нет", str(n3))

        print("\n[4] Описание подписки")
        sub=Subscription(store_id=sid,telegram_id=1,brand="Nike",size="L",max_price=Decimal("150"))
        chk(S.describe(sub)=="Nike · L · до 150", "текст подписки", S.describe(sub))
        chk(S.describe(Subscription(store_id=sid,telegram_id=1))=="любые новинки", "пустая подписка")
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(PostJob).where(PostJob.store_id==sid))
            await s.execute(delete(Subscription).where(Subscription.store_id==sid))
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
