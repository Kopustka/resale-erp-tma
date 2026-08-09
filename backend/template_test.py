"""Живой неразрушающий тест шаблонов постов.

Работает против ASGI-приложения напрямую (без запущенного сервиса) и реальной
БД, НО схему не роняет: создаёт временные user/store/membership и в конце всё
за собой удаляет. В отличие от api_test.py/smoke_test.py — никакого DROP SCHEMA.

Запуск: /opt/resale-venv/bin/python template_test.py
"""
import asyncio
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import PostTemplate, Role, Store, StoreMember, User
from app.services import ai_template, post_template as pt
from app.services import template_capture

settings = get_settings()
TG_ID = 999778
BASE = "http://test/api/v1/templates"

_passed = 0
_failed = 0


def check(cond: bool, name: str, extra: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}" + (f"\n       {extra}" if extra else ""))


def sign(tg_id: int, username: str) -> str:
    user = json.dumps(
        {"id": tg_id, "username": username, "first_name": "TplTest"},
        separators=(",", ":"),
    )
    f = {"auth_date": str(int(time.time())), "user": user, "query_id": "AAF"}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(f.items()))
    sec = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    f["hash"] = hmac.new(sec, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(f)


async def setup_store() -> uuid.UUID:
    async with SessionLocal() as s:
        user = (
            await s.execute(select(User).where(User.telegram_id == TG_ID))
        ).scalar_one_or_none()
        if user is None:
            user = User(telegram_id=TG_ID, username="tpltest", first_name="TplTest")
            s.add(user)
            await s.flush()
        store = Store(name="TPL-TEST-STORE", owner_id=user.id, channel_signature="@seller")
        s.add(store)
        await s.flush()
        s.add(StoreMember(user_id=user.id, store_id=store.id, role=Role.OWNER))
        user.current_store_id = store.id
        await s.commit()
        return store.id


async def teardown() -> None:
    async with SessionLocal() as s:
        user = (
            await s.execute(select(User).where(User.telegram_id == TG_ID))
        ).scalar_one_or_none()
        if user is None:
            return
        stores = (
            await s.execute(select(Store).where(Store.owner_id == user.id))
        ).scalars().all()
        for st in stores:
            await s.execute(delete(PostTemplate).where(PostTemplate.store_id == st.id))
            await s.execute(delete(StoreMember).where(StoreMember.store_id == st.id))
        user.current_store_id = None
        await s.flush()
        for st in stores:
            await s.execute(delete(Store).where(Store.id == st.id))
        await s.execute(delete(User).where(User.id == user.id))
        await s.commit()


# --------------------------------------------------------------------------- #
# Чистые функции рендерера — без БД и сети
# --------------------------------------------------------------------------- #
def test_renderer() -> None:
    print("\n[1] Рендерер")

    ctx = pt.build_context(
        {"title": "Tee", "price": 50, "price_currency": "BYN", "length_cm": 70}, "@me"
    )
    out = pt.render("<b>{title}</b>\n📐 {measurements}\n{signature}", ctx)
    check("Длина 70" in out and "<b>Tee</b>" in out, "подстановка значений")

    out = pt.render("<b>{title}</b>\n📐 {measurements}", pt.build_context({"title": "X"}, None))
    check("📐" not in out, "строка с пустым плейсхолдером выброшена целиком", out)

    out = pt.render("A\n{measurements}\nB", pt.build_context({"title": "X"}, None))
    check(out == "A\nB", "дыра от выброшенной строки схлопнута", repr(out))

    ctx = pt.build_context({"title": "<script>alert(1)</script>"}, None)
    out = pt.render("{title}", ctx)
    check("<script>" not in out and "&lt;script&gt;" in out, "значения экранируются")

    out = pt.render("<b>{title}</b>", pt.build_context({"title": "A & B"}, None))
    check(out == "<b>A &amp; B</b>", "разметка шаблона живая, значение экранировано", out)

    ctx = pt.build_context({"brand": "Stone Island", "category": "Худи", "size": "L"}, None)
    check(ctx["hashtags"] == "#StoneIsland #Худи #L", "хэштеги", ctx["hashtags"])

    ctx = pt.build_context({}, None)
    check("личные сообщения" in ctx["price_line"], "цены нет — строка про личку")

    check(len(pt.render("{description}" * 50, pt.build_context(
        {"description": "x" * 100}, None))) <= pt.MAX_CAPTION, "обрезка до 1024")


def test_validation() -> None:
    print("\n[2] Валидация шаблона")

    for body, why in [
        ("{nonexistent}", "неизвестный плейсхолдер"),
        ("<script>x</script>{title}", "запрещённый тег"),
        ("<b>{title}", "незакрытый тег"),
        ("{title}</b>", "закрывающий без пары"),
        ("   ", "пустой шаблон"),
    ]:
        try:
            pt.validate_body(body)
            check(False, f"отклоняет: {why}", f"пропустил {body!r}")
        except pt.TemplateError:
            check(True, f"отклоняет: {why}")

    for body, why in [
        ("<b>{title}</b>\n{price_line}", "обычный шаблон"),
        ("<a href=\"https://t.me/x\">{title}</a>", "ссылка"),
        ("{title}<br/>{price}", "self-closing br"),
        ("<b><i>{title}</i></b>", "вложенные теги"),
    ]:
        try:
            pt.validate_body(body)
            check(True, f"принимает: {why}")
        except pt.TemplateError as e:
            check(False, f"принимает: {why}", str(e))


def test_repair() -> None:
    print("\n[3] Починка ответа модели")

    out = ai_template.repair("<b>{title}</b> {выдуманный} {alsofake}")
    check("{выдуманный}" not in out and "{alsofake}" not in out, "выдуманные плейсхолдеры убраны", out)

    out = ai_template.repair("<div class='x'>{title}</div>")
    check("<div" not in out and "{title}" in out, "запрещённые теги вырезаны, текст цел", out)

    out = ai_template.repair("<b>{title}\n{price}")
    check("<b>" not in out and "{title}" in out, "битая разметка -> текст без тегов", out)

    try:
        ai_template.repair("{onlyfake}")
        check(False, "пустой после починки -> ошибка")
    except pt.TemplateError:
        check(True, "пустой после починки -> ошибка")


def test_default_matches_legacy() -> None:
    print("\n[4] Дефолтный шаблон = прежнее оформление")
    from app.services.telegram_post import build_caption

    cases = [
        {"title": "T", "description": "D", "price": 1, "price_currency": "BYN",
         "length_cm": 70, "width_cm": 50, "sleeve_cm": 60},
        {"title": "T", "description": "D"},
        {"title": "T", "price": 5, "price_currency": "USD"},
    ]
    for i, it in enumerate(cases):
        legacy_like = pt.render(pt.DEFAULT_TEMPLATE_BODY, pt.build_context(it, "@s"))
        check(build_caption(it, "@s") == legacy_like, f"кейс {i + 1} совпадает")


# --------------------------------------------------------------------------- #
# HTTP + БД
# --------------------------------------------------------------------------- #
async def test_api(store_id: uuid.UUID) -> None:
    print("\n[5] HTTP API")
    headers = {"X-TG-Init-Data": sign(TG_ID, "tpltest")}
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get(f"{BASE}/placeholders", headers=headers)
        check(r.status_code == 200 and len(r.json()) == len(pt.PLACEHOLDERS),
              "GET /placeholders", f"{r.status_code} {r.text[:200]}")

        r = await c.post(f"{BASE}/preview", headers=headers,
                         json={"body": "<b>{title}</b>\n{price_line}"})
        check(r.status_code == 200 and "Archive Nike" in r.json()["caption"],
              "POST /preview на демо-вещи", f"{r.status_code} {r.text[:200]}")

        r = await c.post(f"{BASE}/preview", headers=headers, json={"body": "{fake}"})
        check(r.status_code == 422, "POST /preview отклоняет мусор", str(r.status_code))

        r = await c.post(BASE, headers=headers,
                         json={"name": "Первый", "body": "<b>{title}</b>\n{price_line}"})
        check(r.status_code == 201 and r.json()["is_default"] is True,
              "первый шаблон создаётся активным", f"{r.status_code} {r.text[:200]}")
        first_id = r.json()["id"]

        r = await c.post(BASE, headers=headers,
                         json={"name": "Второй", "body": "{title} — {price}"})
        check(r.status_code == 201 and r.json()["is_default"] is False,
              "второй создаётся неактивным")
        second_id = r.json()["id"]

        r = await c.post(BASE, headers=headers, json={"name": "Плохой", "body": "{fake}"})
        check(r.status_code == 422, "создание с мусором отклонено", str(r.status_code))

        r = await c.get(BASE, headers=headers)
        check(r.status_code == 200 and len(r.json()) == 2, "GET списка вернул 2")
        check(r.json()[0]["is_default"] is True, "активный идёт первым")

        r = await c.post(f"{BASE}/{second_id}/default", headers=headers)
        check(r.status_code == 200 and r.json()["is_default"] is True, "переключение активного")
        r = await c.get(BASE, headers=headers)
        actives = [t["id"] for t in r.json() if t["is_default"]]
        check(actives == [second_id], "активный ровно один", str(actives))

        r = await c.patch(f"{BASE}/{first_id}", headers=headers, json={"name": "Переименован"})
        check(r.status_code == 200 and r.json()["name"] == "Переименован", "PATCH имени")

        r = await c.patch(f"{BASE}/{first_id}", headers=headers, json={"body": "<b>{fake}"})
        check(r.status_code == 422, "PATCH с мусором отклонён")

        # Удаляем активный — активность должна перейти на оставшийся.
        r = await c.delete(f"{BASE}/{second_id}", headers=headers)
        check(r.status_code == 204, "DELETE активного", str(r.status_code))
        r = await c.get(BASE, headers=headers)
        left = r.json()
        check(len(left) == 1 and left[0]["is_default"] is True,
              "активность перешла на оставшийся", str(left))

        r = await c.delete(f"{BASE}/{first_id}", headers=headers)
        check(r.status_code == 204, "DELETE последнего разрешён")
        r = await c.get(BASE, headers=headers)
        check(r.json() == [], "список пуст")

        r = await c.get(f"{BASE}/{uuid.uuid4()}", headers=headers)
        check(r.status_code in (404, 405), "чужой/несуществующий id -> не 200", str(r.status_code))

        # Отсутствие заголовка ловит валидатор FastAPI (422), подпись — auth (401).
        r = await c.get(BASE)
        check(r.status_code == 422, "без заголовка initData -> 422", str(r.status_code))

        r = await c.get(BASE, headers={"X-TG-Init-Data": "user=%7B%7D&hash=deadbeef"})
        check(r.status_code == 401, "поддельная подпись -> 401", str(r.status_code))


async def test_capture(store_id: uuid.UUID) -> None:
    print("\n[6] Сессия захвата примера")
    token = await template_capture.create(TG_ID, store_id)
    data = await template_capture.get(token)
    check(data is not None and data["status"] == template_capture.WAITING, "создана в waiting")

    found = await template_capture.get_active_for_user(TG_ID)
    check(found is not None and found[0] == token, "находится по telegram_id")

    await template_capture.arm(token)
    data = await template_capture.get(token)
    check(data["status"] == template_capture.ARMED, "deep link переводит в armed")

    fake_tpl = uuid.uuid4()
    await template_capture.finish(token, fake_tpl)
    data = await template_capture.get(token)
    check(data["status"] == template_capture.DONE and data["template_id"] == str(fake_tpl),
          "finish проставляет done + template_id")
    check(await template_capture.get_active_for_user(TG_ID) is None,
          "обратный индекс очищен после finish")

    token2 = await template_capture.create(TG_ID, store_id)
    await template_capture.cancel(token2)
    check(await template_capture.get(token2) is None, "cancel убирает сессию")

    check(await template_capture.arm("несуществующий") is None, "arm по мусорному токену -> None")


async def main() -> None:
    print("=" * 62)
    print("ТЕСТ ШАБЛОНОВ ПОСТОВ (неразрушающий)")
    print("=" * 62)
    test_renderer()
    test_validation()
    test_repair()
    test_default_matches_legacy()

    store_id = await setup_store()
    try:
        await test_api(store_id)
        await test_capture(store_id)
    finally:
        await teardown()
        print("\n[cleanup] временные user/store/шаблоны удалены")

    print("\n" + "=" * 62)
    print(f"ИТОГ: {_passed} успешно, {_failed} провалено")
    print("=" * 62)
    raise SystemExit(1 if _failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
