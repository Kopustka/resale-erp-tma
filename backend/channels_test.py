"""Неразрушающий тест мультиканальности.

Проверяет CRUD каналов, постановку публикации во все включённые каналы,
пометку «продано» в каждом, защиту от дублей и нормализацию адреса.
Реальных сообщений в Telegram не шлёт.
"""
import asyncio
import hashlib
import hmac
import json
import sys
import time
import uuid
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    AuditLog, Channel,
    Item,
    ItemPost,
    ItemStatus,
    JobKind,
    PostJob,
    Role,
    Store,
    StoreCounter,
    StoreMember,
    User,
)
from app.routers.channels import _normalize
from app.routers.items import _enqueue_mark_sold, _enqueue_publish

settings = get_settings()
TG_ID = 999784
BASE = "http://test/api/v1/channels"
_passed = _failed = 0


def check(cond, name, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}" + (f"\n       {extra}" if extra else ""))


def sign():
    u = json.dumps(
        {"id": TG_ID, "username": "chtest", "first_name": "Ch"}, separators=(",", ":")
    )
    f = {"auth_date": str(int(time.time())), "user": u, "query_id": "AAF"}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(f.items()))
    sec = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    f["hash"] = hmac.new(sec, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(f)


async def setup():
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one_or_none()
        if u is None:
            u = User(telegram_id=TG_ID, username="chtest", first_name="Ch")
            s.add(u)
            await s.flush()
        st = Store(name="CH-TEST", owner_id=u.id)
        s.add(st)
        await s.flush()
        s.add(StoreMember(user_id=u.id, store_id=st.id, role=Role.OWNER))
        s.add(StoreCounter(store_id=st.id))
        u.current_store_id = st.id
        item = Item(store_id=st.id, sku="#T1", title="Tee", brand="Nike",
                    category="Футболка", status=ItemStatus.LISTED)
        s.add(item)
        await s.commit()
        return st.id, item.id


async def teardown(store_id):
    async with SessionLocal() as s:
        items = (await s.execute(select(Item.id).where(Item.store_id == store_id))).scalars().all()
        if items:
            await s.execute(delete(ItemPost).where(ItemPost.item_id.in_(items)))
        await s.execute(delete(PostJob).where(PostJob.store_id == store_id))
        await s.execute(delete(Channel).where(Channel.store_id == store_id))
        await s.execute(delete(Item).where(Item.store_id == store_id))
        await s.execute(delete(StoreCounter).where(StoreCounter.store_id == store_id))
        await s.execute(delete(StoreMember).where(StoreMember.store_id == store_id))
        u = (await s.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one_or_none()
        if u:
            u.current_store_id = None
            await s.flush()
        await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
        await s.execute(delete(Store).where(Store.id == store_id))
        if u:
            await s.execute(delete(User).where(User.id == u.id))
        await s.commit()


def test_normalize():
    print("\n[1] Нормализация адреса канала")
    cases = [
        ("@shop", "@shop"),
        ("shop", "@shop"),
        ("t.me/shop", "@shop"),
        ("https://t.me/shop", "@shop"),
        ("-1001234567890", "-1001234567890"),
        ("https://t.me/+AbCdEf", "t.me/+AbCdEf"),
    ]
    for raw, want in cases:
        got = _normalize(raw)
        check(got == want, f"{raw!r} -> {want!r}", f"получили {got!r}")


async def test_api(store_id):
    print("\n[2] CRUD каналов")
    h = {"X-TG-Init-Data": sign()}
    tr = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=tr, base_url="http://test") as c:
        r = await c.post(BASE, headers=h, json={"chat_id": "@first", "title": "Основной"})
        check(r.status_code == 201 and r.json()["enabled"] is True, "создание канала",
              f"{r.status_code} {r.text[:150]}")
        first = r.json()["id"]

        r = await c.post(BASE, headers=h, json={"chat_id": "@first"})
        check(r.status_code == 409, "дубль отклонён", str(r.status_code))

        r = await c.post(BASE, headers=h, json={"chat_id": "t.me/second", "signature": "@vtoroy"})
        check(r.status_code == 201 and r.json()["chat_id"] == "@second",
              "второй канал, адрес нормализован", r.text[:120])
        second = r.json()["id"]

        r = await c.get(BASE, headers=h)
        check(r.status_code == 200 and len(r.json()) == 2, "список из двух")

        r = await c.patch(f"{BASE}/{second}", headers=h, json={"enabled": False})
        check(r.status_code == 200 and r.json()["enabled"] is False, "выключение канала")

        r = await c.patch(f"{BASE}/{uuid.uuid4()}", headers=h, json={"enabled": True})
        check(r.status_code == 404, "чужой id -> 404", str(r.status_code))
        return first, second


async def test_publish(store_id, item_id, first, second):
    print("\n[3] Постановка публикации по каналам")
    async with SessionLocal() as s:
        await _enqueue_publish(s, store_id, item_id)
    async with SessionLocal() as s:
        jobs = (await s.execute(
            select(PostJob).where(PostJob.item_id == item_id, PostJob.kind == JobKind.POST_ITEM)
        )).scalars().all()
    check(len(jobs) == 1, "задание только для включённого канала", f"создано {len(jobs)}")
    check(str(jobs[0].channel_uid) == first, "задание указывает на нужный канал")

    async with SessionLocal() as s:
        await _enqueue_publish(s, store_id, item_id)
    async with SessionLocal() as s:
        jobs = (await s.execute(
            select(PostJob).where(PostJob.item_id == item_id, PostJob.kind == JobKind.POST_ITEM)
        )).scalars().all()
    check(len(jobs) == 1, "повторный вызов не плодит дублей", f"стало {len(jobs)}")

    # Включаем второй канал — задание должно появиться только для него
    async with SessionLocal() as s:
        ch = (await s.execute(select(Channel).where(Channel.id == uuid.UUID(second)))).scalar_one()
        ch.enabled = True
        await s.commit()
        await _enqueue_publish(s, store_id, item_id)
    async with SessionLocal() as s:
        jobs = (await s.execute(
            select(PostJob).where(PostJob.item_id == item_id, PostJob.kind == JobKind.POST_ITEM)
        )).scalars().all()
    check(len(jobs) == 2, "включённому каналу добавилось задание", f"стало {len(jobs)}")

    print("\n[4] Пометка «продано» по всем постам")
    async with SessionLocal() as s:
        s.add(ItemPost(item_id=item_id, channel_id=uuid.UUID(first), message_id=111))
        s.add(ItemPost(item_id=item_id, channel_id=uuid.UUID(second), message_id=222))
        await s.commit()
        await _enqueue_mark_sold(s, store_id, item_id)
    async with SessionLocal() as s:
        jobs = (await s.execute(
            select(PostJob).where(PostJob.item_id == item_id, PostJob.kind == JobKind.MARK_SOLD)
        )).scalars().all()
    check(len(jobs) == 2, "задание на каждый опубликованный пост", f"создано {len(jobs)}")
    check({j.message_id for j in jobs} == {111, 222}, "правятся оба сообщения",
          str({j.message_id for j in jobs}))

    async with SessionLocal() as s:
        await _enqueue_mark_sold(s, store_id, item_id)
    async with SessionLocal() as s:
        jobs = (await s.execute(
            select(PostJob).where(PostJob.item_id == item_id, PostJob.kind == JobKind.MARK_SOLD)
        )).scalars().all()
    check(len(jobs) == 2, "повтор не плодит дублей", f"стало {len(jobs)}")

    print("\n[5] Публикация не повторяется для уже опубликованного канала")
    async with SessionLocal() as s:
        await s.execute(delete(PostJob).where(PostJob.item_id == item_id))
        await s.commit()
        await _enqueue_publish(s, store_id, item_id)
    async with SessionLocal() as s:
        jobs = (await s.execute(
            select(PostJob).where(PostJob.item_id == item_id, PostJob.kind == JobKind.POST_ITEM)
        )).scalars().all()
    check(len(jobs) == 0, "оба канала уже имеют пост — заданий нет", f"создано {len(jobs)}")


async def main():
    print("=" * 62)
    print("ТЕСТ МУЛЬТИКАНАЛЬНОСТИ (неразрушающий)")
    print("=" * 62)
    test_normalize()
    store_id, item_id = await setup()
    try:
        first, second = await test_api(store_id)
        await test_publish(store_id, item_id, first, second)
    finally:
        await teardown(store_id)
        print("\n[cleanup] временные данные удалены")
    print("\n" + "=" * 62)
    print(f"ИТОГ: {_passed} успешно, {_failed} провалено")
    print("=" * 62)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
