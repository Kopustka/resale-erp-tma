"""HTTP-тест через ASGI: подпись initData, авторизация, роутеры, идемпотентность.

Запуск: DATABASE_URL=... BOT_TOKEN=<любой валидный по формату> python api_test.py
Требует Postgres + Redis.
"""

# --------------------------------------------------------------------------
# ВНИМАНИЕ: этот скрипт делает DROP SCHEMA public CASCADE — он стирает БД
# целиком. Один запуск по DATABASE_URL продакшена уничтожает весь склад.
# Поэтому по умолчанию он не стартует: нужен явный ALLOW_DESTRUCTIVE_TESTS=1.
# Запускать только на одноразовой базе, никогда — на боевой.
# --------------------------------------------------------------------------
import os as _os
import sys as _sys

if _os.environ.get("ALLOW_DESTRUCTIVE_TESTS") != "1":
    _sys.exit(
        "ОТКАЗ: скрипт стирает базу целиком (DROP SCHEMA).\n"
        "Запуск только на одноразовой БД: ALLOW_DESTRUCTIVE_TESTS=1 "
        "DATABASE_URL=<тестовая> python " + _os.path.basename(__file__)
    )

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlencode

import httpx
from sqlalchemy import text

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Role, Store, StoreMember, User

settings = get_settings()
TG_ID = 555


def sign_init_data(tg_id: int, username: str) -> str:
    """Формирует валидный initData, как это делает Telegram."""
    user = json.dumps(
        {"id": tg_id, "username": username, "first_name": "Test"},
        separators=(",", ":"),
    )
    fields = {"auth_date": str(int(time.time())), "user": user, "query_id": "AAF"}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    fields["hash"] = h
    return urlencode(fields)


async def seed():
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as s:
        u = User(telegram_id=TG_ID, username="tester", first_name="Test")
        s.add(u)
        await s.flush()
        st = Store(name="Тест-склад", owner_id=u.id)
        s.add(st)
        await s.flush()
        s.add(StoreMember(user_id=u.id, store_id=st.id, role=Role.OWNER))
        u.current_store_id = st.id
        await s.commit()


async def main():
    await seed()
    init_data = sign_init_data(TG_ID, "tester")
    headers = {"X-TG-Init-Data": init_data}
    checks = []

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        # 0. health
        r = await c.get("/health")
        assert r.status_code == 200

        # 1. плохая подпись -> 401
        r = await c.get("/api/v1/stores", headers={"X-TG-Init-Data": init_data + "x"})
        assert r.status_code == 401, r.status_code
        checks.append("Bad signature -> 401")

        # 2. список складов
        r = await c.get("/api/v1/stores", headers=headers)
        assert r.status_code == 200 and r.json()[0]["role"] == "OWNER"
        checks.append(f"GET /stores -> {r.json()[0]['name']} (OWNER)")

        # 3. создание товара
        payload = {
            "title": "Куртка Carhartt", "brand": "carhartt", "category": "куртки",
            "cost_price": 60, "restore_cost": 10, "delivery_cost": 5,
            "purchase_location": "Секонд Б",
        }
        r = await c.post("/api/v1/items", headers=headers, json=payload)
        assert r.status_code == 201, r.text
        item = r.json()
        assert item["sku"].startswith("#") and item["status"] == "BOUGHT"
        checks.append(f"POST /items -> {item['sku']} v{item['version']}")

        # 4. свайп статуса с Idempotency-Key
        idem = str(uuid.uuid4())
        h2 = {**headers, "Idempotency-Key": idem}
        r = await c.patch(
            f"/api/v1/items/{item['id']}/status",
            headers=h2, json={"version": item["version"]},
        )
        assert r.status_code == 200, r.text
        after = r.json()
        assert after["status"] == "PREPARING" and after["version"] == 1
        checks.append(f"PATCH status (swipe) -> {after['status']} v{after['version']}")

        # 5. повтор того же Idempotency-Key -> тот же результат, без двойного перехода
        r = await c.patch(
            f"/api/v1/items/{item['id']}/status",
            headers=h2, json={"version": item["version"]},
        )
        assert r.status_code == 200 and r.json()["status"] == "PREPARING"
        checks.append("Idempotent replay -> same result (no double transition)")

        # 6. конфликт версий -> 409
        r = await c.patch(
            f"/api/v1/items/{item['id']}/status",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"version": 0},
        )
        assert r.status_code == 409, r.status_code
        checks.append("Stale version -> 409 Conflict")

        # 7. недопустимый переход -> 409
        r = await c.patch(
            f"/api/v1/items/{item['id']}/status",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"version": 1, "target_status": "SOLD"},
        )
        assert r.status_code == 409, r.status_code
        checks.append("Illegal transition PREPARING->SOLD -> 409")

        # 7.5. откат статуса на шаг назад (PREPARING -> BOUGHT)
        r = await c.patch(
            f"/api/v1/items/{item['id']}/status",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"version": 1, "target_status": "BOUGHT"},
        )
        assert r.status_code == 200, r.text
        rb = r.json()
        assert rb["status"] == "BOUGHT" and rb["listed_date"] is None and rb["sold_date"] is None
        checks.append("Rollback PREPARING->BOUGHT -> 200, даты сброшены")
        # вернём вперёд, чтобы дальнейшие шаги не менялись
        r = await c.patch(
            f"/api/v1/items/{item['id']}/status",
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
            json={"version": rb["version"], "target_status": "PREPARING"},
        )
        assert r.status_code == 200

        # 8. список товаров (cursor page)
        r = await c.get("/api/v1/items?limit=10", headers=headers)
        assert r.status_code == 200 and len(r.json()["items"]) == 1
        checks.append(f"GET /items -> {len(r.json()['items'])} item(s)")

        # 9. Голосовой разбор для формы создания
        r = await c.post(
            "/api/v1/items/parse-voice", headers=headers,
            json={"text": "кархарт куртка размер эль чёрная состояние восемь из десяти закупка сто двадцать"},
        )
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["brand"] == "Carhartt" and v["category"] == "куртки"
        assert v["size"] == "L" and v["color"] == "чёрный"
        assert v["condition"] == "8/10" and v["cost_price"] == 120
        checks.append(
            f"POST /parse-voice -> {v['brand']}/{v['category']} size={v['size']} "
            f"color={v['color']} cond={v['condition']} cost={v['cost_price']}"
        )

        # 10. аналитика
        r = await c.get("/api/v1/analytics/summary", headers=headers)
        assert r.status_code == 200
        checks.append(f"GET /analytics/summary -> active={r.json()['active_count']}")

        # 11. приглашение по юзернейму (pending)
        r = await c.post(
            "/api/v1/stores/invites", headers=headers,
            json={"username": "@newguy", "role": "EMPLOYEE"},
        )
        assert r.status_code == 201 and r.json()["status"] == "PENDING"
        checks.append(f"POST /invites @newguy -> {r.json()['status']}")

        iid = item["id"]
        # 12. редактирование (в т.ч. состояние/condition и цена)
        r = await c.patch(
            f"/api/v1/items/{iid}", headers=headers,
            json={"title": "Изменённый", "condition": "9/10", "cost_price": 55,
                  "length_cm": 72, "width_cm": 58.5, "sleeve_cm": 65},
        )
        assert r.status_code == 200, r.text
        e = r.json()
        assert e["title"] == "Изменённый" and e["condition"] == "9/10" and e["cost_price"] == 55
        assert e["length_cm"] == 72 and e["width_cm"] == 58.5 and e["sleeve_cm"] == 65
        checks.append("PATCH /items/{id} -> title/condition/cost + замеры 72/58.5/65 см")

        # 13. архивация -> исчез из активных, виден в архиве
        r = await c.delete(f"/api/v1/items/{iid}", headers=headers)
        assert r.status_code == 204
        active = (await c.get("/api/v1/items", headers=headers)).json()["items"]
        arch = (await c.get("/api/v1/items?archived=true", headers=headers)).json()["items"]
        assert all(i["id"] != iid for i in active) and any(i["id"] == iid for i in arch)
        checks.append("DELETE (archive) -> нет в активных, есть в архиве")

        # 14. восстановление из архива
        r = await c.post(f"/api/v1/items/{iid}/restore", headers=headers)
        assert r.status_code == 200
        active = (await c.get("/api/v1/items", headers=headers)).json()["items"]
        assert any(i["id"] == iid for i in active)
        checks.append("POST /restore -> снова в активных")

        # 15. безвозвратное удаление
        r = await c.delete(f"/api/v1/items/{iid}?hard=true", headers=headers)
        assert r.status_code == 204
        r = await c.get(f"/api/v1/items/{iid}", headers=headers)
        assert r.status_code == 404
        checks.append("DELETE ?hard=true -> товар удалён навсегда (404)")

    print("\n".join(f"  ✓ {x}" for x in checks))
    print("\nAPI TEST PASSED ✅")


if __name__ == "__main__":
    asyncio.run(main())
