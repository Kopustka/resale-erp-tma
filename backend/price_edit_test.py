"""Неразрушающий тест автоправки поста при смене цены."""
import asyncio, sys, uuid
from decimal import Decimal
from sqlalchemy import delete, select
from app.db import SessionLocal
from app.models import (Channel, Item, ItemPost, ItemStatus, JobKind, PostJob,
                        Role, Store, StoreCounter, StoreMember, User)
from app.routers.items import _enqueue_price_edit

TG=999786; ok=bad=0
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)

async def main():
    global ok,bad
    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="pe",first_name="PE"); s.add(u); await s.flush()
        st=Store(name="PRICE-TEST",owner_id=u.id); s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); s.add(StoreCounter(store_id=st.id))
        u.current_store_id=st.id
        ch=Channel(store_id=st.id,chat_id="@pe"); s.add(ch)
        it=Item(store_id=st.id,sku="#P1",title="T",brand="N",category="C",status=ItemStatus.LISTED)
        s.add(it); await s.commit()
        sid, cid, iid = st.id, ch.id, it.id
        s.add(ItemPost(item_id=iid,channel_id=cid,message_id=555)); await s.commit()
    try:
        print("\n[1] Снижение цены")
        async with SessionLocal() as s:
            await _enqueue_price_edit(s, sid, iid, Decimal("200"), Decimal("150"))
        async with SessionLocal() as s:
            jobs=(await s.execute(select(PostJob).where(PostJob.kind==JobKind.EDIT_CAPTION))).scalars().all()
        chk(len(jobs)==1, "задание создано", str(len(jobs)))
        chk(jobs[0].message_id==555, "правится нужное сообщение")
        chk("СКИДКА" in (jobs[0].caption_prefix or ""), "плашка скидки", str(jobs[0].caption_prefix))
        chk("25%" in (jobs[0].caption_prefix or ""), "процент посчитан верно", str(jobs[0].caption_prefix))

        print("\n[2] Повышение цены — без плашки")
        async with SessionLocal() as s:
            await s.execute(delete(PostJob).where(PostJob.store_id==sid)); await s.commit()
            await _enqueue_price_edit(s, sid, iid, Decimal("100"), Decimal("180"))
        async with SessionLocal() as s:
            jobs=(await s.execute(select(PostJob).where(PostJob.kind==JobKind.EDIT_CAPTION))).scalars().all()
        chk(len(jobs)==1 and not jobs[0].caption_prefix, "пост правится, плашки нет", str(jobs[0].caption_prefix))

        print("\n[3] Проданный пост не трогаем")
        async with SessionLocal() as s:
            await s.execute(delete(PostJob).where(PostJob.store_id==sid))
            p=(await s.execute(select(ItemPost).where(ItemPost.item_id==iid))).scalar_one()
            p.sold_marked=True; await s.commit()
            await _enqueue_price_edit(s, sid, iid, Decimal("200"), Decimal("100"))
        async with SessionLocal() as s:
            jobs=(await s.execute(select(PostJob).where(PostJob.kind==JobKind.EDIT_CAPTION))).scalars().all()
        chk(len(jobs)==0, "для проданного заданий нет", str(len(jobs)))

        print("\n[4] Нет опубликованных постов — тишина")
        async with SessionLocal() as s:
            await s.execute(delete(ItemPost).where(ItemPost.item_id==iid))
            await s.execute(delete(PostJob).where(PostJob.store_id==sid)); await s.commit()
            await _enqueue_price_edit(s, sid, iid, Decimal("200"), Decimal("100"))
        async with SessionLocal() as s:
            jobs=(await s.execute(select(PostJob).where(PostJob.store_id==sid))).scalars().all()
        chk(len(jobs)==0, "заданий не создано")
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(ItemPost).where(ItemPost.item_id==iid))
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
