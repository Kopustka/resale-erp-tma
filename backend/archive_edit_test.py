"""Живой неразрушающий тест: правка, архив, список архива, восстановление, удаление.

Против запущенного сервиса (API_BASE). Создаёт временный склад, в конце всё чистит.
Запуск: API_BASE=https://... DATABASE_URL=...(прод resale) BOT_TOKEN=<реальный> python archive_edit_test.py
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    StoreField, AuditLog, Item,
    ItemStatusLog,
    Role,
    Store,
    StoreCounter,
    StoreInvite,
    StoreMember,
    User,
)

settings = get_settings()
API = os.getenv("API_BASE", "http://127.0.0.1:8020")
TG_ID = 999778


def sign(tg_id: int, username: str) -> str:
    user = json.dumps({"id": tg_id, "username": username, "first_name": "AE"}, separators=(",", ":"))
    f = {"auth_date": str(int(time.time())), "user": user, "query_id": "AAF"}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(f.items()))
    sec = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    f["hash"] = hmac.new(sec, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(f)


async def setup():
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one_or_none()
        if u is None:
            u = User(telegram_id=TG_ID, username="aetest", first_name="AE")
            s.add(u)
            await s.flush()
        st = Store(name="AE-TEST-STORE", owner_id=u.id)
        s.add(st)
        await s.flush()
        s.add(StoreMember(user_id=u.id, store_id=st.id, role=Role.OWNER))
        u.current_store_id = st.id
        await s.commit()


async def teardown():
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one_or_none()
        if not u:
            return
        stores = (await s.execute(select(Store).where(Store.owner_id == u.id))).scalars().all()
        for st in stores:
            ids = (await s.execute(select(Item.id).where(Item.store_id == st.id))).scalars().all()
            if ids:
                await s.execute(delete(ItemStatusLog).where(ItemStatusLog.item_id.in_(ids)))
            await s.execute(delete(Item).where(Item.store_id == st.id))
            await s.execute(delete(StoreMember).where(StoreMember.store_id == st.id))
            await s.execute(delete(StoreCounter).where(StoreCounter.store_id == st.id))
            await s.execute(delete(StoreInvite).where(StoreInvite.store_id == st.id))
        u.current_store_id = None
        await s.flush()
        for st in stores:
            await s.execute(delete(AuditLog).where(AuditLog.store_id == st.id))
            await s.execute(delete(StoreField).where(StoreField.store_id == st.id))
            await s.execute(delete(Store).where(Store.id == st.id))
        await s.execute(delete(User).where(User.id == u.id))
        await s.commit()


async def main():
    await setup()
    h = {"X-TG-Init-Data": sign(TG_ID, "aetest")}
    checks = []
    try:
        async with httpx.AsyncClient(base_url=API) as c:
            r = await c.post("/api/v1/items", headers=h, json={"title": "T", "brand": "b", "category": "c", "cost_price": 30})
            assert r.status_code == 201, r.text
            iid = r.json()["id"]

            # правка (в т.ч. состояние и цена, деньги как числа)
            r = await c.patch(f"/api/v1/items/{iid}", headers=h, json={"title": "Отредактирован", "condition": "7/10", "cost_price": 42})
            assert r.status_code == 200, r.text
            e = r.json()
            assert e["title"] == "Отредактирован" and e["condition"] == "7/10"
            assert e["cost_price"] == 42 and isinstance(e["cost_price"], (int, float))
            checks.append("PATCH правка (title/condition/cost как число)")

            # архив
            assert (await c.delete(f"/api/v1/items/{iid}", headers=h)).status_code == 204
            act = (await c.get("/api/v1/items", headers=h)).json()["items"]
            arc = (await c.get("/api/v1/items?archived=true", headers=h)).json()["items"]
            assert all(i["id"] != iid for i in act) and any(i["id"] == iid for i in arc)
            checks.append("Архив: нет в активных, есть в ?archived=true")

            # восстановление
            assert (await c.post(f"/api/v1/items/{iid}/restore", headers=h)).status_code == 200
            act = (await c.get("/api/v1/items", headers=h)).json()["items"]
            assert any(i["id"] == iid for i in act)
            checks.append("Восстановление: снова в активных")

            # жёсткое удаление
            assert (await c.delete(f"/api/v1/items/{iid}?hard=true", headers=h)).status_code == 204
            assert (await c.get(f"/api/v1/items/{iid}", headers=h)).status_code == 404
            checks.append("Удаление навсегда -> 404")

        print("\n".join(f"  ✓ {x}" for x in checks))
        print("\nARCHIVE/EDIT TEST PASSED ✅")
    finally:
        await teardown()
        print("  (временный склад удалён)")


if __name__ == "__main__":
    asyncio.run(main())
