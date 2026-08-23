"""Надзор за чужим складом: согласие, границы доступа, отзыв.

Главное, что здесь проверяется, — доступ не появляется от одного желания
наблюдателя. Пока владелец не нажал «Разрешить», чужая лента закрыта, и
после отзыва закрывается снова.
"""
import asyncio, sys, uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (AuditLog, Item, ItemStatus, ItemStatusLog, OversightStatus,
                        Role, Store, StoreCounter, StoreMember, StoreOversight, User)
from app.routers import admin
from app.schemas import OversightRequest
from app.services import audit
from app.services import oversight as ov

TG_BOSS, TG_SOLO, TG_EMP = 999921, 999922, 999923
ok = bad = 0


def chk(c, n, e=""):
    global ok, bad
    print(("  ✅ " if c else "  ❌ ") + n + ("" if c else f"\n       {e}"))
    if c:
        ok += 1
    else:
        bad += 1


async def expect_403(coro, name):
    try:
        await coro
        chk(False, name, "доступ дали, а не должны были")
    except HTTPException as e:
        chk(e.status_code == 403, name, f"код {e.status_code}")


async def main():
    global ok, bad
    async with SessionLocal() as s:
        users = {}
        for tg, uname, fn in ((TG_BOSS, "ov_boss", "Босс"),
                              (TG_SOLO, "ov_solo", "Соло"),
                              (TG_EMP, "ov_emp", "Наёмник")):
            u = (await s.execute(select(User).where(User.telegram_id == tg))).scalar_one_or_none()
            if u is None:
                u = User(telegram_id=tg, username=uname, first_name=fn)
                s.add(u)
                await s.flush()
            u.username = uname
            users[tg] = u

        # Два независимых склада: у наблюдателя свой, у поднадзорного свой.
        boss_store = Store(name="OV-BOSS", owner_id=users[TG_BOSS].id)
        solo_store = Store(name="OV-SOLO", owner_id=users[TG_SOLO].id)
        s.add_all([boss_store, solo_store])
        await s.flush()
        s.add_all([StoreCounter(store_id=boss_store.id), StoreCounter(store_id=solo_store.id)])
        s.add_all([
            StoreMember(user_id=users[TG_BOSS].id, store_id=boss_store.id, role=Role.OWNER),
            StoreMember(user_id=users[TG_SOLO].id, store_id=solo_store.id, role=Role.OWNER),
            StoreMember(user_id=users[TG_EMP].id, store_id=boss_store.id, role=Role.EMPLOYEE),
        ])
        users[TG_BOSS].current_store_id = boss_store.id
        users[TG_SOLO].current_store_id = solo_store.id
        users[TG_EMP].current_store_id = boss_store.id

        it = Item(store_id=solo_store.id, sku="#S1", title="Своя вещь", brand="B",
                  category="C", status=ItemStatus.BOUGHT, purchaser_id=users[TG_SOLO].id)
        s.add(it)
        await s.flush()
        s.add(ItemStatusLog(item_id=it.id, old_status=None, new_status=ItemStatus.BOUGHT,
                            changed_by=users[TG_SOLO].id))
        audit.record(s, store_id=solo_store.id, user_id=users[TG_SOLO].id,
                     action=audit.ITEM_CREATE, summary="#S1 Своя вещь")
        await s.commit()
        boss_id, solo_id = boss_store.id, solo_store.id
        u_boss, u_solo, u_emp = users[TG_BOSS].id, users[TG_SOLO].id, users[TG_EMP].id

    req_id = None
    try:
        print("\n[1] Без запроса чужой склад закрыт")
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            await expect_403(
                admin.resolve_scope(store_id=solo_id, user=boss, session=s),
                "владелец другого склада не видит чужой",
            )
            own = await admin.resolve_scope(store_id=None, user=boss, session=s)
            chk(own.store_id == boss_id and own.as_owner, "свой склад открыт как владелец")

        print("\n[2] Сотрудник не проходит вообще")
        async with SessionLocal() as s:
            emp = (await s.execute(select(User).where(User.id == u_emp))).scalar_one()
            await expect_403(
                admin.resolve_scope(store_id=None, user=emp, session=s),
                "сотрудник не открывает панель своего склада",
            )
            await expect_403(
                admin.resolve_scope(store_id=solo_id, user=emp, session=s),
                "и чужого тоже",
            )

        print("\n[3] Запрос создаётся в ожидании, доступа ещё нет")
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            out = await admin.request_oversight(
                payload=OversightRequest(username="@OV_SOLO"), user=boss, session=s
            )
            req_id = out.id
        chk(out.status == "PENDING", "статус PENDING", out.status)
        chk(out.target_username == "ov_solo", "юзернейм нормализован", out.target_username)
        chk(out.store_id is None, "склад ещё не привязан")
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            await expect_403(
                admin.resolve_scope(store_id=solo_id, user=boss, session=s),
                "пока не согласился — доступа нет",
            )
            chk(await ov.can_watch(s, u_boss, solo_id) is False, "can_watch=False")

        print("\n[4] Повторный запрос отбивается")
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            try:
                await admin.request_oversight(
                    payload=OversightRequest(username="ov_solo"), user=boss, session=s
                )
                chk(False, "второй запрос отклонён")
            except HTTPException as e:
                chk(e.status_code == 409, "второй запрос отклонён (409)", str(e.status_code))
            try:
                await admin.request_oversight(
                    payload=OversightRequest(username="ov_boss"), user=boss, session=s
                )
                chk(False, "нельзя запросить самого себя")
            except HTTPException as e:
                chk(e.status_code == 422, "нельзя запросить самого себя (422)", str(e.status_code))

        print("\n[5] Запрос видит адресат, и только он")
        async with SessionLocal() as s:
            solo = (await s.execute(select(User).where(User.id == u_solo))).scalar_one()
            emp = (await s.execute(select(User).where(User.id == u_emp))).scalar_one()
            mine = await ov.pending_for(s, solo)
            theirs = await ov.pending_for(s, emp)
        chk(len(mine) == 1 and mine[0].id == req_id, "адресат видит свой запрос")
        chk(theirs == [], "посторонний не видит чужой запрос")

        print("\n[6] Согласие открывает ленту")
        async with SessionLocal() as s:
            solo = (await s.execute(select(User).where(User.id == u_solo))).scalar_one()
            req = (await s.execute(
                select(StoreOversight).where(StoreOversight.id == req_id))).scalar_one()
            store = await ov.accept(s, req, solo)
            await s.commit()
        chk(store is not None and store.id == solo_id, "привязался склад адресата")
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            sc = await admin.resolve_scope(store_id=solo_id, user=boss, session=s)
        chk(sc.store_id == solo_id, "чужой склад открылся")
        chk(sc.as_owner is False, "но не как владелец — режим наблюдения")

        print("\n[7] Наблюдателю видна лента, но не финансы")
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            sc = await admin.resolve_scope(store_id=solo_id, user=boss, session=s)
            page = await admin.activity(scope=sc, session=s, user_id=None, group=None,
                                        days=30, limit=50, offset=0)
            team = await admin.team(scope=sc, session=s, days=30)
        chk(len(page.events) == 2, "видно 2 события чужого склада", str(len(page.events)))
        chk(any("#S1" in e.summary for e in page.events), "видно артикул вещи")
        fields = set(admin.MemberStats.model_fields)
        money = {"cost", "profit", "revenue", "margin", "price"}
        chk(not any(any(m in f for m in money) for f in fields),
            "в сводке нет денежных полей", str(sorted(fields)))
        chk([m.username for m in team.members] == ["ov_solo"], "в команде чужого склада он один",
            str([m.username for m in team.members]))

        print("\n[8] Наблюдение не даёт доступа к другим складам")
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            solo = (await s.execute(select(User).where(User.id == u_solo))).scalar_one()
            await expect_403(
                admin.resolve_scope(store_id=boss_id, user=solo, session=s),
                "доступ односторонний: поднадзорный не видит наблюдателя",
            )
            scs = await admin.scopes(user=boss, session=s)
        kinds = {sc.name: sc.kind for sc in scs}
        chk(kinds == {"OV-BOSS": "own", "OV-SOLO": "watch"}, "в списке складов свой и поднадзорный",
            str(kinds))

        print("\n[9] Владелец закрывает доступ")
        async with SessionLocal() as s:
            solo = (await s.execute(select(User).where(User.id == u_solo))).scalar_one()
            await admin.drop_oversight(req_id=req_id, user=solo, session=s)
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            await expect_403(
                admin.resolve_scope(store_id=solo_id, user=boss, session=s),
                "после отзыва лента снова закрыта",
            )
            scs = await admin.scopes(user=boss, session=s)
            chk([sc.kind for sc in scs] == ["own"], "поднадзорный пропал из списка",
                str([sc.name for sc in scs]))
            chk(await ov.can_watch(s, u_boss, solo_id) is False, "can_watch=False после отзыва")

        print("\n[10] Отзыв вправе сделать каждая сторона, но не посторонний")
        async with SessionLocal() as s:
            req2 = StoreOversight(watcher_id=u_boss, target_username="ov_solo",
                                  store_id=solo_id, status=OversightStatus.ACTIVE)
            s.add(req2)
            await s.commit()
            rid2 = req2.id
        async with SessionLocal() as s:
            emp = (await s.execute(select(User).where(User.id == u_emp))).scalar_one()
            try:
                await admin.drop_oversight(req_id=rid2, user=emp, session=s)
                chk(False, "посторонний не может отозвать")
            except HTTPException as e:
                chk(e.status_code == 403, "посторонний не может отозвать (403)", str(e.status_code))
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            await admin.drop_oversight(req_id=rid2, user=boss, session=s)
        async with SessionLocal() as s:
            r = (await s.execute(select(StoreOversight).where(StoreOversight.id == rid2))).scalar_one()
        chk(r.status == OversightStatus.REVOKED, "наблюдатель отказался сам", r.status.value)

        print("\n[11] Согласие без своего склада невозможно")
        async with SessionLocal() as s:
            lone = User(telegram_id=999924, username="ov_lone", first_name="Один")
            s.add(lone)
            await s.flush()
            req3 = StoreOversight(watcher_id=u_boss, target_username="ov_lone")
            s.add(req3)
            await s.flush()
            store = await ov.accept(s, req3, lone)
            await s.rollback()
        chk(store is None, "без склада согласиться нечем")
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(StoreOversight).where(
                StoreOversight.watcher_id.in_([u_boss, u_solo, u_emp])))
            await s.execute(delete(AuditLog).where(AuditLog.store_id.in_([boss_id, solo_id])))
            iids = (await s.execute(select(Item.id).where(
                Item.store_id.in_([boss_id, solo_id])))).scalars().all()
            await s.execute(delete(ItemStatusLog).where(ItemStatusLog.item_id.in_(iids)))
            await s.execute(delete(Item).where(Item.store_id.in_([boss_id, solo_id])))
            await s.execute(delete(StoreCounter).where(StoreCounter.store_id.in_([boss_id, solo_id])))
            await s.execute(delete(StoreMember).where(StoreMember.store_id.in_([boss_id, solo_id])))
            for tg in (TG_BOSS, TG_SOLO, TG_EMP, 999924):
                u = (await s.execute(select(User).where(User.telegram_id == tg))).scalar_one_or_none()
                if u is not None:
                    u.current_store_id = None
            await s.flush()
            await s.execute(delete(Store).where(Store.id.in_([boss_id, solo_id])))
            await s.execute(delete(User).where(
                User.telegram_id.in_([TG_BOSS, TG_SOLO, TG_EMP, 999924])))
            await s.commit()
        print("\n[cleanup] ok")
    print(f"\nИТОГ: {ok} успешно, {bad} провалено")
    sys.exit(1 if bad else 0)


asyncio.run(main())
