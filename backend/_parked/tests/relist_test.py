"""Тест повторной публикации: снятие с канала при откате и новый пост."""
import asyncio, sys, uuid
from sqlalchemy import delete, select
from app.db import SessionLocal
from app.models import (Channel, Item, ItemPost, ItemStatus, JobKind, JobStatus,
                        PostJob, Role, Store, StoreCounter, StoreMember, User)
from app.routers.items import _enqueue_publish, _enqueue_unpublish

TG=999793; ok=bad=0
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)

async def jobs_of(sid, kind):
    async with SessionLocal() as s:
        return (await s.execute(select(PostJob).where(
            PostJob.store_id==sid, PostJob.kind==kind))).scalars().all()

async def main():
    global ok,bad
    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="rl",first_name="RL"); s.add(u); await s.flush()
        st=Store(name="RELIST-TEST",owner_id=u.id); s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); s.add(StoreCounter(store_id=st.id))
        u.current_store_id=st.id
        ch=Channel(store_id=st.id,chat_id="@rl"); s.add(ch); await s.flush()
        it=Item(store_id=st.id,sku="#R1",title="T",brand="N",category="C",
                status=ItemStatus.LISTED,photo_file_ids=["FILEID"])
        s.add(it); await s.commit()
        sid,cid,iid=st.id,ch.id,it.id
    try:
        print("\n[1] Первая публикация")
        async with SessionLocal() as s:
            await _enqueue_publish(s, sid, iid)
        chk(len(await jobs_of(sid, JobKind.POST_ITEM))==1, "задание создано")

        # имитируем успешную публикацию воркером
        async with SessionLocal() as s:
            s.add(ItemPost(item_id=iid, channel_id=cid, message_id=777))
            for j in (await s.execute(select(PostJob).where(PostJob.store_id==sid))).scalars():
                j.status=JobStatus.DONE
            await s.commit()

        print("\n[2] Повторное выставление без снятия — дубля быть не должно")
        async with SessionLocal() as s:
            await _enqueue_publish(s, sid, iid)
        chk(len(await jobs_of(sid, JobKind.POST_ITEM))==1, "нового задания нет (защита от дублей)")

        print("\n[3] Откат до «отфотографирован» снимает с публикации")
        async with SessionLocal() as s:
            await _enqueue_unpublish(s, sid, iid)
        un = await jobs_of(sid, JobKind.UNPUBLISH)
        chk(len(un)==1, "задание на снятие создано", str(len(un)))
        chk(un[0].message_id==777, "снимается нужное сообщение", str(un[0].message_id))

        async with SessionLocal() as s:
            await _enqueue_unpublish(s, sid, iid)
        chk(len(await jobs_of(sid, JobKind.UNPUBLISH))==1, "повтор не плодит дублей")

        print("\n[4] После снятия выставление снова работает")
        # имитируем отработку снятия воркером
        async with SessionLocal() as s:
            await s.execute(delete(ItemPost).where(ItemPost.item_id==iid))
            for j in (await s.execute(select(PostJob).where(PostJob.store_id==sid))).scalars():
                j.status=JobStatus.DONE
            await s.commit()
            await _enqueue_publish(s, sid, iid)
        posts_jobs=[j for j in await jobs_of(sid, JobKind.POST_ITEM) if j.status!=JobStatus.DONE]
        chk(len(posts_jobs)==1, "новое задание на публикацию появилось", str(len(posts_jobs)))

        print("\n[5] Снятие без публикаций — тишина")
        async with SessionLocal() as s:
            await s.execute(delete(PostJob).where(PostJob.store_id==sid)); await s.commit()
            await _enqueue_unpublish(s, sid, iid)
        chk(len(await jobs_of(sid, JobKind.UNPUBLISH))==0, "заданий не создано")
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
