"""Тест запрета публикации без цены в объявлении."""
import asyncio, hashlib, hmac, json, sys, time, uuid
from decimal import Decimal
from urllib.parse import urlencode
import httpx
from sqlalchemy import delete, select
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import (AuditLog, Channel, Item, ItemPost, ItemStatus, ItemStatusLog, PostJob,
                        Role, Store, StoreCounter, StoreMember, User)

s_=get_settings(); TG=999794; ok=bad=0
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)
def sign():
    u=json.dumps({"id":TG,"username":"pg","first_name":"PG"},separators=(",",":"))
    f={"auth_date":str(int(time.time())),"user":u,"query_id":"AAF"}
    d="\n".join(f"{k}={v}" for k,v in sorted(f.items()))
    sec=hmac.new(b"WebAppData",s_.bot_token.encode(),hashlib.sha256).digest()
    f["hash"]=hmac.new(sec,d.encode(),hashlib.sha256).hexdigest(); return urlencode(f)

async def main():
    global ok,bad
    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="pg",first_name="PG"); s.add(u); await s.flush()
        st=Store(name="PRICE-GUARD",owner_id=u.id); s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); s.add(StoreCounter(store_id=st.id))
        u.current_store_id=st.id
        ch=Channel(store_id=st.id,chat_id="@pg"); s.add(ch)
        free=Item(store_id=st.id,sku="#N1",title="Без цены",brand="N",category="C",
                  status=ItemStatus.PHOTOGRAPHED,photo_file_ids=["FID"])
        paid=Item(store_id=st.id,sku="#N2",title="С ценой",brand="N",category="C",
                  status=ItemStatus.PHOTOGRAPHED,photo_file_ids=["FID"],
                  list_price_orig=Decimal("150"))
        s.add(free); s.add(paid); await s.commit()
        sid,fid,pid=st.id,free.id,paid.id
    h={"X-TG-Init-Data":sign()}
    B="http://test/api/v1"
    try:
        tr=httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=tr, base_url="http://test") as c:
            print("\n[1] Выставление без цены")
            hh=dict(h); hh["Idempotency-Key"]=str(uuid.uuid4())
            async with SessionLocal() as s:
                fver=(await s.execute(select(Item.version).where(Item.id==fid))).scalar_one()
            r=await c.patch(f"{B}/items/{fid}/status", headers=hh,
                            json={"target_status":"LISTED","version":fver})
            chk(r.status_code==422, "отклонено с 422", str(r.status_code))
            chk("цену" in r.text.lower(), "текст объясняет причину", r.text[:120])
            async with SessionLocal() as s:
                it=(await s.execute(select(Item).where(Item.id==fid))).scalar_one()
            chk(it.status==ItemStatus.PHOTOGRAPHED, "статус НЕ изменился", it.status.value)
            async with SessionLocal() as s:
                jobs=(await s.execute(select(PostJob).where(PostJob.store_id==sid))).scalars().all()
            chk(len(jobs)==0, "задание на публикацию не создано", str(len(jobs)))

            print("\n[2] Выставление с ценой")
            async with SessionLocal() as s:
                pver=(await s.execute(select(Item.version).where(Item.id==pid))).scalar_one()
            hh=dict(h); hh["Idempotency-Key"]=str(uuid.uuid4())
            r=await c.patch(f"{B}/items/{pid}/status", headers=hh,
                            json={"target_status":"LISTED","version":pver})
            chk(r.status_code==200, "прошло", f"{r.status_code} {r.text[:120]}")
            async with SessionLocal() as s:
                jobs=(await s.execute(select(PostJob).where(PostJob.store_id==sid))).scalars().all()
            chk(len(jobs)==1, "задание создано", str(len(jobs)))

            print("\n[3] Появилась цена — выставление разблокировалось")
            r=await c.patch(f"{B}/items/{fid}", headers=h, json={"list_price":99})
            chk(r.status_code==200, "цена сохранена")
            async with SessionLocal() as s:
                it=(await s.execute(select(Item).where(Item.id==fid))).scalar_one()
                ver=it.version
            hh=dict(h); hh["Idempotency-Key"]=str(uuid.uuid4())
            r=await c.patch(f"{B}/items/{fid}/status", headers=hh,
                            json={"target_status":"LISTED","version":ver})
            chk(r.status_code==200, "теперь выставляется", f"{r.status_code} {r.text[:120]}")

            print("\n[4] Дроп без цены")
            async with SessionLocal() as s:
                it=(await s.execute(select(Item).where(Item.id==pid))).scalar_one()
                bare=Item(store_id=sid,sku="#N3",title="Пустая",brand="N",category="C",
                          status=ItemStatus.PHOTOGRAPHED,photo_file_ids=["FID"])
                s.add(bare); await s.commit(); bid=bare.id
            r=await c.post(f"{B}/drops", headers=h, json={"item_ids":[str(pid),str(bid)]})
            chk(r.status_code==422 and "#N3" in r.text, "дроп отклонён с указанием вещи", r.text[:140])
            r=await c.post(f"{B}/drops", headers=h, json={"item_ids":[str(pid)]})
            chk(r.status_code==201, "дроп только из вещей с ценой проходит", str(r.status_code))
    finally:
        async with SessionLocal() as s:
            ids=(await s.execute(select(Item.id).where(Item.store_id==sid))).scalars().all()
            if ids:
                await s.execute(delete(ItemPost).where(ItemPost.item_id.in_(ids)))
                await s.execute(delete(ItemStatusLog).where(ItemStatusLog.item_id.in_(ids)))
            from app.models import Drop, DropItem
            dids=(await s.execute(select(Drop.id).where(Drop.store_id==sid))).scalars().all()
            if dids: await s.execute(delete(DropItem).where(DropItem.drop_id.in_(dids)))
            await s.execute(delete(Drop).where(Drop.store_id==sid))
            await s.execute(delete(PostJob).where(PostJob.store_id==sid))
            await s.execute(delete(Channel).where(Channel.store_id==sid))
            await s.execute(delete(Item).where(Item.store_id==sid))
            await s.execute(delete(StoreCounter).where(StoreCounter.store_id==sid))
            await s.execute(delete(StoreMember).where(StoreMember.store_id==sid))
            u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one()
            u.current_store_id=None; await s.flush()
            await s.execute(delete(AuditLog).where(AuditLog.store_id == sid))
            await s.execute(delete(Store).where(Store.id==sid))
            await s.execute(delete(User).where(User.id==u.id)); await s.commit()
        print("\n[cleanup] ok")
    print(f"\nИТОГ: {ok} успешно, {bad} провалено")
    sys.exit(1 if bad else 0)

asyncio.run(main())
