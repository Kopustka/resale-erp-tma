"""Живой неразрушающий тест загрузки фото из галереи.

Работает против запущенного сервиса на 127.0.0.1:8020 и реальной БД,
НО не роняет схему: создаёт временные user/store/membership, в конце всё удаляет.

Запуск: DATABASE_URL=... BOT_TOKEN=<реальный> python upload_test.py
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
    Item,
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
TG_ID = 999777

# 1x1 PNG (валидный)
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da6360000002000154a24f5f0000000049454e44ae426082"
)


def sign(tg_id: int, username: str) -> str:
    user = json.dumps({"id": tg_id, "username": username, "first_name": "UpTest"}, separators=(",", ":"))
    f = {"auth_date": str(int(time.time())), "user": user, "query_id": "AAF"}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(f.items()))
    sec = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    f["hash"] = hmac.new(sec, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(f)


async def setup_store() -> str:
    async with SessionLocal() as s:
        user = (await s.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one_or_none()
        if user is None:
            user = User(telegram_id=TG_ID, username="uptest", first_name="UpTest")
            s.add(user)
            await s.flush()
        store = Store(name="UPLOAD-TEST-STORE", owner_id=user.id)
        s.add(store)
        await s.flush()
        s.add(StoreMember(user_id=user.id, store_id=store.id, role=Role.OWNER))
        user.current_store_id = store.id
        await s.commit()
        return str(store.id)


async def teardown(media_name: str | None):
    from pathlib import Path
    async with SessionLocal() as s:
        user = (await s.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one_or_none()
        if user:
            stores = (await s.execute(select(Store).where(Store.owner_id == user.id))).scalars().all()
            for st in stores:
                item_ids = (
                    await s.execute(select(Item.id).where(Item.store_id == st.id))
                ).scalars().all()
                if item_ids:
                    await s.execute(delete(ItemStatusLog).where(ItemStatusLog.item_id.in_(item_ids)))
                await s.execute(delete(Item).where(Item.store_id == st.id))
                await s.execute(delete(StoreMember).where(StoreMember.store_id == st.id))
                await s.execute(delete(StoreCounter).where(StoreCounter.store_id == st.id))
                await s.execute(delete(StoreInvite).where(StoreInvite.store_id == st.id))
            user.current_store_id = None
            await s.flush()
            for st in stores:
                await s.execute(delete(Store).where(Store.id == st.id))
            await s.execute(delete(User).where(User.id == user.id))
            await s.commit()
    if media_name:
        p = Path(settings.media_dir) / media_name
        if p.exists():
            p.unlink()


async def main():
    await setup_store()
    headers = {"X-TG-Init-Data": sign(TG_ID, "uptest")}
    checks = []
    media_name = None
    try:
        async with httpx.AsyncClient(base_url=API) as c:
            # 1. загрузка фото
            r = await c.post(
                "/api/v1/media/upload",
                headers=headers,
                files={"file": ("photo.png", PNG, "image/png")},
            )
            assert r.status_code == 200, r.text
            photo_id = r.json()["photo_id"]
            assert photo_id.startswith("local:"), photo_id
            media_name = photo_id[len("local:"):]
            checks.append(f"upload -> {photo_id}")

            # 2. неверный тип -> 422
            r = await c.post(
                "/api/v1/media/upload",
                headers=headers,
                files={"file": ("doc.txt", b"hello", "text/plain")},
            )
            assert r.status_code == 422, r.status_code
            checks.append("non-image -> 422")

            # 3. создаём товар с этим фото
            r = await c.post(
                "/api/v1/items",
                headers=headers,
                json={"title": "Фото-тест", "brand": "test", "category": "тест", "photo_file_ids": [photo_id]},
            )
            assert r.status_code == 201, r.text
            item = r.json()
            assert item["photo_count"] == 1, item
            checks.append(f"item created with photo, sku={item['sku']}")

            # 4. отдача через прокси -> те же байты
            r = await c.get(f"/api/v1/media/{item['id']}/0", headers=headers)
            assert r.status_code == 200, r.status_code
            assert r.content == PNG, "bytes mismatch"
            assert r.headers.get("content-type", "").startswith("image/"), r.headers.get("content-type")
            checks.append(f"media proxy serves local file ({len(r.content)} bytes, ok)")

        print("\n".join(f"  ✓ {x}" for x in checks))
        print("\nUPLOAD TEST PASSED ✅")
    finally:
        await teardown(media_name)
        print("  (временные данные и файл удалены)")


if __name__ == "__main__":
    asyncio.run(main())
