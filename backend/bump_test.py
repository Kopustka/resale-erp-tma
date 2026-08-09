"""Неразрушающий тест автоподнятия зависших вещей."""
import asyncio, sys
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from app.db import SessionLocal
from app.models import (Channel, Item, ItemPost, ItemStatus, JobKind, JobStatus,
                        PostJob, Role, Store, StoreCounter, StoreMember, User)
from app.services.post_worker import scan_for_bumps

TG=999787; ok=bad=0
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)

NOW = datetime.now(timezone.utc)

async def mk_item(s, sid, cid, sku, *, days_listed, status=ItemStatus.LISTED,
                  bumped_days_ago=None, sold_marked=False, archived=False, with_post=True):
    it = Item(store_id=sid, sku=sku, title="T", brand="N", category="C", status=status,
              listed_date=NOW - timedelta(days=days_listed),
              bumped_at=None if bumped_days_ago is None else NOW - timedelta(days=bumped_days_ago),
              archived_at=NOW if archived else None)
    s.add(it); await s.flush()
    if with_post:
        s.add(ItemPost(item_id=it.id, channel_id=cid, message_id=1000, sold_marked=sold_marked))
    return it

async def main():
    global ok,bad
    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="b",first_name="B"); s.add(u); await s.flush()
        st=Store(name="BUMP-TEST",owner_id=u.id,bump_enabled=True,bump_after_days=60)
        s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); s.add(StoreCounter(store_id=st.id))
        u.current_store_id=st.id
        ch=Channel(store_id=st.id,chat_id="@b"); s.add(ch); await s.flush()
        sid,cid=st.id,ch.id
        await mk_item(s,sid,cid,"#B1",days_listed=90)                       # должен подняться
        await mk_item(s,sid,cid,"#B2",days_listed=10)                       # свежий
        await mk_item(s,sid,cid,"#B3",days_listed=90,bumped_days_ago=2)     # недавно поднимали
        await mk_item(s,sid,cid,"#B4",days_listed=90,bumped_days_ago=30)    # давно поднимали -> можно
        await mk_item(s,sid,cid,"#B5",days_listed=90,sold_marked=True)      # продан
        await mk_item(s,sid,cid,"#B6",days_listed=90,archived=True)         # в архиве
        await mk_item(s,sid,cid,"#B7",days_listed=90,status=ItemStatus.BOOKED)  # забронирован
        await mk_item(s,sid,cid,"#B8",days_listed=90,with_post=False)       # не публиковался
        await s.commit()
    try:
        print("\n[1] Отбор кандидатов")
        async with SessionLocal() as s:
            n = await scan_for_bumps(s)
        async with SessionLocal() as s:
            jobs=(await s.execute(select(PostJob, Item.sku).join(Item, Item.id==PostJob.item_id)
                                  .where(PostJob.kind==JobKind.BUMP))).all()
        skus = sorted(sku for _, sku in jobs)
        chk(skus == ["#B1","#B4"], "подняты только зависшие вне кулдауна", f"получили {skus}")
        chk(n == 2, "счётчик совпадает", str(n))

        print("\n[2] Повторный скан не дублирует")
        async with SessionLocal() as s:
            n2 = await scan_for_bumps(s)
        chk(n2 == 0, "второй проход пуст", str(n2))

        print("\n[3] Выключенный автобамп")
        async with SessionLocal() as s:
            await s.execute(delete(PostJob).where(PostJob.store_id==sid))
            st=(await s.execute(select(Store).where(Store.id==sid))).scalar_one()
            st.bump_enabled=False; await s.commit()
            n3 = await scan_for_bumps(s)
        chk(n3 == 0, "у склада с выключенным бампом заданий нет", str(n3))

        print("\n[4] Выключенный канал")
        async with SessionLocal() as s:
            st=(await s.execute(select(Store).where(Store.id==sid))).scalar_one()
            st.bump_enabled=True
            ch=(await s.execute(select(Channel).where(Channel.id==cid))).scalar_one()
            ch.enabled=False; await s.commit()
            n4 = await scan_for_bumps(s)
        chk(n4 == 0, "в выключенный канал не поднимаем", str(n4))

        print("\n[5] Порог склада учитывается")
        async with SessionLocal() as s:
            ch=(await s.execute(select(Channel).where(Channel.id==cid))).scalar_one()
            ch.enabled=True
            st=(await s.execute(select(Store).where(Store.id==sid))).scalar_one()
            st.bump_after_days=5; await s.commit()
            n5 = await scan_for_bumps(s)
        async with SessionLocal() as s:
            jobs=(await s.execute(select(Item.sku).join(PostJob, PostJob.item_id==Item.id)
                                  .where(PostJob.kind==JobKind.BUMP))).scalars().all()
        chk("#B2" in jobs, "при пороге 5 дней подхватилась и свежая вещь", str(sorted(jobs)))
    finally:
        async with SessionLocal() as s:
            ids=(await s.execute(select(Item.id).where(Item.store_id==sid))).scalars().all()
            if ids: await s.execute(delete(ItemPost).where(ItemPost.item_id.in_(ids)))
            await s.execute(delete(PostJob).where(PostJob.store_id==sid))
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
