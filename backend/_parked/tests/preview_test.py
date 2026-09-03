"""Неразрушающий тест предпросмотра перед публикацией."""
import asyncio, sys, uuid
from sqlalchemy import delete, select
from app.db import SessionLocal
from app.models import (Channel, Item, ItemStatus, JobKind, JobStatus, PostJob,
                        Role, Store, StoreCounter, StoreMember, User)
from app.services import post_queue, preview

TG=999788; ok=bad=0
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)

async def main():
    global ok,bad
    print("\n[1] callback_data влезает в лимит Telegram")
    kb = preview.keyboard(uuid.uuid4())
    for row in kb["inline_keyboard"]:
        for btn in row:
            n = len(btn["callback_data"].encode())
            chk(n <= 64, f"{btn['callback_data'][:12]}… = {n} байт (лимит 64)")

    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="pv",first_name="PV"); s.add(u); await s.flush()
        st=Store(name="PREVIEW-TEST",owner_id=u.id,preview_before_post=True); s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); s.add(StoreCounter(store_id=st.id))
        u.current_store_id=st.id
        ch=Channel(store_id=st.id,chat_id="@pv"); s.add(ch); await s.flush()
        it=Item(store_id=st.id,sku="#V1",title="T",brand="N",category="C",status=ItemStatus.LISTED)
        s.add(it); await s.commit()
        sid,cid,iid=st.id,ch.id,it.id
    try:
        print("\n[2] Задание в AWAITING воркер не забирает")
        async with SessionLocal() as s:
            j = await post_queue.enqueue(s, store_id=sid, kind=JobKind.POST_ITEM,
                                         channel_id="@pv", channel_uid=cid, item_id=iid)
            j.status = JobStatus.AWAITING
            await s.commit()
        async with SessionLocal() as s:
            got = await post_queue.claim(s, limit=10)
        chk(all(x.item_id != iid for x in got), "ожидающее задание не захвачено", str(len(got)))

        print("\n[3] Подтверждение переводит в очередь")
        async with SessionLocal() as s:
            jobs=(await s.execute(select(PostJob).where(PostJob.item_id==iid,
                  PostJob.status==JobStatus.AWAITING))).scalars().all()
            for x in jobs: x.status = JobStatus.PENDING
            await s.commit()
        async with SessionLocal() as s:
            got = await post_queue.claim(s, limit=10)
        chk(any(x.item_id == iid for x in got), "после подтверждения задание пошло в работу")

        print("\n[4] Отмена")
        async with SessionLocal() as s:
            await s.execute(delete(PostJob).where(PostJob.store_id==sid))
            j = await post_queue.enqueue(s, store_id=sid, kind=JobKind.POST_ITEM,
                                         channel_id="@pv", channel_uid=cid, item_id=iid)
            j.status = JobStatus.AWAITING
            await s.commit()
            jobs=(await s.execute(select(PostJob).where(PostJob.item_id==iid))).scalars().all()
            for x in jobs: x.status = JobStatus.CANCELLED
            await s.commit()
        async with SessionLocal() as s:
            got = await post_queue.claim(s, limit=10)
        chk(all(x.item_id != iid for x in got), "отменённое не публикуется")

        print("\n[5] Провал отправки в личку не подвешивает вещь")
        # send_preview на несуществующий chat_id должен вернуть False, а не упасть
        okk = await preview.send_preview(1, iid, "текст", None, 1)
        chk(okk is False, "недоступная личка -> False, без исключения", str(okk))
    finally:
        async with SessionLocal() as s:
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
