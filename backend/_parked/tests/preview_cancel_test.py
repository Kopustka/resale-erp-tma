"""Отмена публикации в боте возвращает вещь в «Сфотографирован»."""
import asyncio, sys, uuid
from sqlalchemy import delete, select
from app.db import SessionLocal
from app.models import (Channel, Item, ItemStatus, ItemStatusLog, JobKind, JobStatus,
                        PostJob, Role, Store, StoreCounter, StoreMember, User)
from app.repositories.items import ItemRepository
from app.services.fsm import prev_status

TG=999901; ok=bad=0
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)

async def main():
    global ok,bad
    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="pc",first_name="PC"); s.add(u); await s.flush()
        st=Store(name="PREVIEW-CANCEL",owner_id=u.id,preview_before_post=True); s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); s.add(StoreCounter(store_id=st.id))
        u.current_store_id=st.id
        ch=Channel(store_id=st.id,chat_id="@pc"); s.add(ch); await s.flush()
        it=Item(store_id=st.id,sku="#C1",title="T",brand="N",category="C",
                status=ItemStatus.LISTED,photo_file_ids=["FID"])
        s.add(it); await s.flush()
        j=PostJob(store_id=st.id,item_id=it.id,kind=JobKind.POST_ITEM,
                  channel_id="@pc",channel_uid=ch.id,status=JobStatus.AWAITING)
        s.add(j); await s.commit()
        sid,iid,uid=st.id,it.id,u.id
    try:
        print("\n[1] Отмена возвращает статус")
        async with SessionLocal() as s:
            item=(await s.execute(select(Item).where(Item.id==iid))).scalar_one()
            back=prev_status(ItemStatus.LISTED)
            repo=ItemRepository(s)
            done=await repo.apply_status(item, back, expected_version=item.version, changed_by=uid)
            for j in (await s.execute(select(PostJob).where(PostJob.item_id==iid))).scalars():
                j.status=JobStatus.CANCELLED
            await s.commit()
        chk(done, "переход применён")
        async with SessionLocal() as s:
            item=(await s.execute(select(Item).where(Item.id==iid))).scalar_one()
        chk(item.status==ItemStatus.PHOTOGRAPHED, "стал «Сфотографирован»", item.status.value)
        chk(item.listed_date is None, "дата выставления сброшена", str(item.listed_date))

        print("\n[2] Задания сняты")
        async with SessionLocal() as s:
            jobs=(await s.execute(select(PostJob).where(PostJob.item_id==iid))).scalars().all()
        chk(all(j.status==JobStatus.CANCELLED for j in jobs), "публикация не уйдёт")

        print("\n[3] Повторное выставление снова возможно")
        async with SessionLocal() as s:
            item=(await s.execute(select(Item).where(Item.id==iid))).scalar_one()
            repo=ItemRepository(s)
            again=await repo.apply_status(item, ItemStatus.LISTED,
                                          expected_version=item.version, changed_by=uid)
            await s.commit()
        chk(again, "вещь снова выставляется")

        print("\n[4] Аудит записан")
        async with SessionLocal() as s:
            logs=(await s.execute(select(ItemStatusLog).where(ItemStatusLog.item_id==iid))).scalars().all()
        pairs=[(l.old_status.value if l.old_status else None, l.new_status.value) for l in logs]
        chk(("LISTED","PHOTOGRAPHED") in pairs, "откат виден в истории", str(pairs))
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(ItemStatusLog).where(ItemStatusLog.item_id==iid))
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
