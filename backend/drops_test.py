"""Неразрушающий тест подборок (дропов). Реальных постов не шлёт."""
import asyncio, hashlib, hmac, json, sys, time, uuid
from decimal import Decimal
from urllib.parse import urlencode
import httpx
from sqlalchemy import delete, select
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import (AuditLog, Channel, Drop, DropItem, Item, ItemStatus, JobKind,
                        PostJob, Role, Store, StoreCounter, StoreMember, User)
from app.services import drops as D

s_=get_settings(); TG=999791; BASE="http://test/api/v1/drops"; ok=bad=0
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)

def sign():
    u=json.dumps({"id":TG,"username":"dr","first_name":"DR"},separators=(",",":"))
    f={"auth_date":str(int(time.time())),"user":u,"query_id":"AAF"}
    d="\n".join(f"{k}={v}" for k,v in sorted(f.items()))
    sec=hmac.new(b"WebAppData",s_.bot_token.encode(),hashlib.sha256).digest()
    f["hash"]=hmac.new(sec,d.encode(),hashlib.sha256).hexdigest(); return urlencode(f)

async def main():
    global ok,bad
    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="dr",first_name="DR"); s.add(u); await s.flush()
        st=Store(name="DROP-TEST",owner_id=u.id,channel_signature="@shop"); s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); s.add(StoreCounter(store_id=st.id))
        u.current_store_id=st.id
        ch=Channel(store_id=st.id,chat_id="@dr"); s.add(ch); await s.flush()
        mk=lambda sku,ph,price=None,size=None: Item(
            store_id=st.id,sku=sku,title=f"Вещь {sku}",brand="Nike",category="C",size=size,
            status=ItemStatus.PHOTOGRAPHED,photo_file_ids=ph,
            list_price_orig=Decimal(price) if price else None)
        a=mk("#D1",["local:297963e114cd41598b0f14b9f8550e93.jpg"],"100","L")   # реальный файл на диске
        b=mk("#D2",["AgACAgIAAxkBAA_fakeFileId"],"200","M")  # telegram file_id
        c=mk("#D3",[])  # без фото
        for x in (a,b,c): s.add(x)
        await s.commit()
        sid,cid,aid,bid,cid_item = st.id, ch.id, a.id, b.id, c.id
    h={"X-TG-Init-Data":sign()}
    try:
        tr=httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=tr, base_url="http://test") as cl:
            print("\n[1] Создание подборки")
            r=await cl.post(BASE, headers=h, json={"item_ids":[str(aid),str(bid)],"title":"Летний дроп"})
            chk(r.status_code==201, "создан", f"{r.status_code} {r.text[:150]}")
            d=r.json(); chk(d["item_count"]==2 and d["channels"]==1, "состав и каналы", str(d))
            async with SessionLocal() as s:
                jobs=(await s.execute(select(PostJob).where(
                    PostJob.kind==JobKind.DROP_POST, PostJob.store_id==sid))).scalars().all()
            chk(len(jobs)==1 and jobs[0].drop_id is not None, "задание поставлено", str(len(jobs)))

            print("\n[2] Проверки на входе")
            r=await cl.post(BASE, headers=h, json={"item_ids":[str(cid_item)]})
            chk(r.status_code==422, "вещь без фото -> 422", str(r.status_code))
            r=await cl.post(BASE, headers=h, json={"item_ids":[str(uuid.uuid4())]})
            chk(r.status_code==404, "чужой id -> 404", str(r.status_code))
            r=await cl.post(BASE, headers=h, json={"item_ids":[str(aid)]*1, "title":"x"})
            chk(r.status_code==201, "одна вещь — тоже дроп")
            r=await cl.post(BASE, headers=h, json={"item_ids":[str(aid)]*12})
            chk(r.status_code==422, "больше 10 -> 422", str(r.status_code))

        print("\n[3] Подпись альбома")
        async with SessionLocal() as s:
            dr=(await s.execute(select(Drop).where(Drop.store_id==sid).limit(1))).scalars().first()
            drop, items = await D.load(s, dr.id)
            cap = D.build_caption(drop, items, "@shop")
        chk("<b>Летний дроп</b>" in cap or "<b>x</b>" in cap, "заголовок жирным", cap[:60])
        chk("1. " in cap, "нумерация есть", cap[:80])
        chk(len(cap) <= 1024, "укладывается в лимит подписи", str(len(cap)))

        print("\n[4] Отбор фото")
        async with SessionLocal() as s:
            all_items=(await s.execute(select(Item).where(Item.store_id==sid).order_by(Item.sku))).scalars().all()
        entries = D.photo_entries(all_items, None)
        chk(len(entries)==2, "вещь без фото в альбом не попала", f"{len(entries)} из {len(all_items)}")
        chk(entries[0][1] is not None, "локальный файл прочитан как байты")
        chk(entries[1][0] is not None and entries[1][1] is None, "file_id передан ссылкой, без скачивания")

        print("\n[5] Пропавший с диска файл")
        all_items[0].photo_file_ids = ["local:нет-такого.jpg"]
        gone = D.photo_entries(all_items, None)
        chk(len(gone)==1, "битую запись пропускаем, альбом не падает", str(len(gone)))
    finally:
        async with SessionLocal() as s:
            ids=(await s.execute(select(Item.id).where(Item.store_id==sid))).scalars().all()
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
