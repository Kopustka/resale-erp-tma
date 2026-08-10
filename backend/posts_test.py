"""Неразрушающий тест контент-календаря."""
import asyncio, hashlib, hmac, json, sys, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import httpx
from sqlalchemy import delete, select
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import (Channel, CustomPost, CustomPostStatus, JobKind, JobStatus,
                        PostJob, Role, Store, StoreMember, User)
from app.services import post_queue

s_=get_settings(); TG=999792; BASE="http://test/api/v1/posts"; ok=bad=0
def chk(c,n,e=""):
    global ok,bad
    print(("  ✅ " if c else "  ❌ ")+n+("" if c else f"\n       {e}"))
    ok,bad=(ok+1,bad) if c else (ok,bad+1)
def sign():
    u=json.dumps({"id":TG,"username":"cp","first_name":"CP"},separators=(",",":"))
    f={"auth_date":str(int(time.time())),"user":u,"query_id":"AAF"}
    d="\n".join(f"{k}={v}" for k,v in sorted(f.items()))
    sec=hmac.new(b"WebAppData",s_.bot_token.encode(),hashlib.sha256).digest()
    f["hash"]=hmac.new(sec,d.encode(),hashlib.sha256).hexdigest(); return urlencode(f)

async def main():
    global ok,bad
    async with SessionLocal() as s:
        u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one_or_none()
        if u is None: u=User(telegram_id=TG,username="cp",first_name="CP"); s.add(u); await s.flush()
        st=Store(name="POSTS-TEST",owner_id=u.id); s.add(st); await s.flush()
        s.add(StoreMember(user_id=u.id,store_id=st.id,role=Role.OWNER)); u.current_store_id=st.id
        ch=Channel(store_id=st.id,chat_id="@cp"); s.add(ch); await s.commit()
        sid=st.id
    h={"X-TG-Init-Data":sign()}
    try:
        tr=httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=tr, base_url="http://test") as cl:
            print("\n[1] Отложенный пост")
            when=(datetime.now(timezone.utc)+timedelta(hours=3)).isoformat()
            r=await cl.post(BASE,headers=h,json={"body":"Завтра ресток","scheduled_at":when})
            chk(r.status_code==201, "создан", f"{r.status_code} {r.text[:150]}")
            pid=r.json()["id"]
            async with SessionLocal() as s:
                job=(await s.execute(select(PostJob).where(
                    PostJob.custom_post_id!=None, PostJob.store_id==sid))).scalars().first()
            chk(job is not None and job.status==JobStatus.PENDING, "задание поставлено")
            chk(job.run_after > datetime.now(timezone.utc)+timedelta(hours=2),
                "запуск отложен на нужное время", str(job.run_after))
            got=await post_queue.claim.__wrapped__ if False else None
            async with SessionLocal() as s:
                taken=await post_queue.claim(s, limit=10)
            chk(all(j.custom_post_id is None for j in taken), "воркер пока его не берёт")

            print("\n[2] Немедленный пост")
            r=await cl.post(BASE,headers=h,json={"body":"Сейчас"})
            chk(r.status_code==201 and r.json()["scheduled_at"] is None, "без даты — сразу")

            print("\n[3] Проверки времени")
            past=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
            r=await cl.post(BASE,headers=h,json={"body":"x","scheduled_at":past})
            chk(r.status_code==422, "прошлое отклонено", str(r.status_code))
            far=(datetime.now(timezone.utc)+timedelta(days=400)).isoformat()
            r=await cl.post(BASE,headers=h,json={"body":"x","scheduled_at":far})
            chk(r.status_code==422, "слишком далёкое будущее отклонено", str(r.status_code))
            soon=(datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat()
            r=await cl.post(BASE,headers=h,json={"body":"x","scheduled_at":soon})
            chk(r.status_code==201, "секунды в прошлом — допуск, форма заполнялась", str(r.status_code))

            print("\n[4] Список и отмена")
            r=await cl.get(BASE,headers=h)
            chk(r.status_code==200 and len(r.json())>=3, "список запланированных", str(len(r.json())))
            r=await cl.delete(f"{BASE}/{pid}",headers=h)
            chk(r.status_code==204, "отменён", str(r.status_code))
            async with SessionLocal() as s:
                p=(await s.execute(select(CustomPost).where(CustomPost.id==pid))).scalar_one()
                jobs=(await s.execute(select(PostJob).where(PostJob.custom_post_id==p.id))).scalars().all()
            chk(p.status==CustomPostStatus.CANCELLED, "статус CANCELLED")
            chk(all(j.status==JobStatus.CANCELLED for j in jobs), "задания сняты из очереди",
                str([j.status.value for j in jobs]))

            print("\n[5] Нет каналов — некуда публиковать")
            async with SessionLocal() as s:
                c=(await s.execute(select(Channel).where(Channel.store_id==sid))).scalar_one()
                c.enabled=False; await s.commit()
            r=await cl.post(BASE,headers=h,json={"body":"x"})
            chk(r.status_code==422, "отказ с понятной причиной", str(r.status_code))
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(PostJob).where(PostJob.store_id==sid))
            await s.execute(delete(CustomPost).where(CustomPost.store_id==sid))
            await s.execute(delete(Channel).where(Channel.store_id==sid))
            await s.execute(delete(StoreMember).where(StoreMember.store_id==sid))
            u=(await s.execute(select(User).where(User.telegram_id==TG))).scalar_one()
            u.current_store_id=None; await s.flush()
            await s.execute(delete(Store).where(Store.id==sid))
            await s.execute(delete(User).where(User.id==u.id)); await s.commit()
        print("\n[cleanup] ok")
    print(f"\nИТОГ: {ok} успешно, {bad} провалено")
    sys.exit(1 if bad else 0)

asyncio.run(main())
