"""Неразрушающий тест скидок. Реальных сообщений в Telegram не шлёт."""
import asyncio, hashlib, hmac, json, sys, time, uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode
import httpx
from sqlalchemy import delete, select
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import (Channel, Discount, DiscountStatus, Item, ItemPost, ItemStatus,
                        JobKind, JobStatus, PostJob, Role, Store, StoreCounter,
                        StoreMember, User)
from app.services import discounts as svc

s_=get_settings(); TG=999795; B="http://test/api/v1/discounts"; ok=bad=0
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)
def sign():
    u=json.dumps({"id":TG,"username":"dc","first_name":"DC"},separators=(",",":"))
    f={"auth_date":str(int(time.time())),"user":u,"query_id":"AAF"}
    d="\n".join(f"{k}={v}" for k,v in sorted(f.items()))
    sec=hmac.new(b"WebAppData",s_.bot_token.encode(),hashlib.sha256).digest()
    f["hash"]=hmac.new(sec,d.encode(),hashlib.sha256).hexdigest(); return urlencode(f)

def test_math():
    print("\n[1] Округление и проценты")
    chk(svc.apply_percent(Decimal("149"),30)==Decimal("104"), "149 −30% -> 104 (не 104.30)",
        str(svc.apply_percent(Decimal("149"),30)))
    chk(svc.apply_percent(Decimal("150"),20)==Decimal("120"), "150 −20% -> 120")
    chk(svc.apply_percent(Decimal("99"),10)==Decimal("89"), "99 −10% -> 89 (вниз, не 90)",
        str(svc.apply_percent(Decimal("99"),10)))
    chk(svc.round_price(Decimal("104.99"))==Decimal("104"), "копеек не остаётся")
    chk(svc.percent_of(Decimal("150"),Decimal("105"))==30, "процент считается по ценам")
    chk(svc.percent_of(Decimal("0"),Decimal("0"))==0, "нулевая цена не роняет расчёт")

async def main():
    global ok,bad
    test_math()
    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="dc",first_name="DC"); s.add(u); await s.flush()
        st=Store(name="DISCOUNT-TEST",owner_id=u.id); s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); s.add(StoreCounter(store_id=st.id))
        u.current_store_id=st.id
        ch=Channel(store_id=st.id,chat_id="@dc"); s.add(ch); await s.flush()
        it=Item(store_id=st.id,sku="#D1",title="Худи",brand="Nike",category="C",
                status=ItemStatus.LISTED,list_price_orig=Decimal("150"),
                list_price=Decimal("150"),price_currency="BYN",cost_currency="BYN")
        s.add(it); await s.flush()
        s.add(ItemPost(item_id=it.id,channel_id=ch.id,message_id=555))
        await s.commit(); sid,iid,cid=st.id,it.id,ch.id
    h={"X-TG-Init-Data":sign()}
    try:
        tr=httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=tr, base_url="http://test") as c:
            print("\n[2] Скидка сразу")
            r=await c.post(B, headers=h, json={"item_id":str(iid),"new_price":105})
            chk(r.status_code==201, "создана", f"{r.status_code} {r.text[:150]}")
            chk(r.json()["percent"]==30, "процент 30", str(r.json().get("percent")))
            async with SessionLocal() as s:
                fresh=(await s.execute(select(Item).where(Item.id==iid))).scalar_one()
            chk(fresh.list_price_orig==Decimal("105"), "цена вещи обновилась", str(fresh.list_price_orig))
            chk(fresh.price_before_discount==Decimal("150"), "старая цена сохранена",
                str(fresh.price_before_discount))
            async with SessionLocal() as s:
                jobs=(await s.execute(select(PostJob).where(PostJob.store_id==sid,
                      PostJob.kind==JobKind.DISCOUNT_POST))).scalars().all()
            chk(len(jobs)==1, "задание на объявление создано", str(len(jobs)))
            chk(jobs[0].message_id==555, "ответ повесится на пост вещи", str(jobs[0].message_id))

            print("\n[3] Проверки на входе")
            r=await c.post(B, headers=h, json={"item_id":str(iid),"new_price":200})
            chk(r.status_code==422 and "меньше" in r.text, "цена выше текущей отклонена", r.text[:120])
            r=await c.post(B, headers=h, json={"item_id":str(iid),"new_price":105})
            chk(r.status_code==422, "цена равная текущей отклонена", str(r.status_code))
            r=await c.post(B, headers=h, json={"item_id":str(uuid.uuid4()),"new_price":10})
            chk(r.status_code==404, "чужая вещь -> 404", str(r.status_code))
            past=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
            r=await c.post(B, headers=h, json={"item_id":str(iid),"new_price":50,"scheduled_at":past})
            chk(r.status_code==422, "прошлое отклонено", str(r.status_code))

            print("\n[4] Отложенная скидка")
            when=(datetime.now(timezone.utc)+timedelta(hours=5)).isoformat()
            r=await c.post(B, headers=h, json={"item_id":str(iid),"new_price":80,"scheduled_at":when})
            chk(r.status_code==201, "запланирована", f"{r.status_code} {r.text[:120]}")
            did=r.json()["id"]
            async with SessionLocal() as s:
                fresh=(await s.execute(select(Item).where(Item.id==iid))).scalar_one()
            chk(fresh.list_price_orig==Decimal("105"),
                "цена НЕ меняется до срока — вещь ещё продаётся по старой",
                str(fresh.list_price_orig))
            async with SessionLocal() as s:
                job=(await s.execute(select(PostJob).where(PostJob.discount_id==uuid.UUID(did)))).scalar_one()
            chk(job.run_after > datetime.now(timezone.utc)+timedelta(hours=4), "запуск отложен")
            async with SessionLocal() as s:
                taken=await __import__("app.services.post_queue", fromlist=["x"]).claim(s, limit=10)
            chk(all(j.discount_id != uuid.UUID(did) for j in taken), "воркер её пока не берёт")

            print("\n[5] Отмена")
            r=await c.delete(f"{B}/{did}", headers=h)
            chk(r.status_code==204, "отменена", str(r.status_code))
            async with SessionLocal() as s:
                d=(await s.execute(select(Discount).where(Discount.id==uuid.UUID(did)))).scalar_one()
                jobs=(await s.execute(select(PostJob).where(PostJob.discount_id==d.id))).scalars().all()
            chk(d.status==DiscountStatus.CANCELLED, "статус CANCELLED")
            chk(all(j.status==JobStatus.CANCELLED for j in jobs), "задания сняты")

            print("\n[6] Список")
            r=await c.get(B, headers=h, params={"item_id":str(iid),"include_done":True})
            chk(r.status_code==200 and len(r.json())>=2, "история по вещи", str(len(r.json())))
            chk(r.json()[0]["item_sku"]=="#D1", "в списке видно артикул")
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(Discount).where(Discount.store_id==sid))
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
