"""Подключение чужого склада к панели: привязка, границы доступа, отзыв.

Подключение происходит сразу — договорённость между людьми складывается
вне программы. Проверяем, что при этом не протекает ничего лишнего: чужой
склад закрыт, пока его не подключили явно, финансы не отдаются никогда, а
после отзыва доступ пропадает.
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
TG_LATER, TG_LONE, TG_DENY = 999925, 999926, 999927
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

        print("\n[3] Запрос ждёт согласия владельца склада")
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            out = await admin.request_oversight(
                payload=OversightRequest(username="@OV_SOLO"), user=boss, session=s
            )
            req_id = out.id
        chk(out.status == "PENDING", "статус PENDING до согласия", out.status)
        chk(out.target_username == "ov_solo", "юзернейм нормализован", out.target_username)
        chk(out.store_id is None, "склад не привязан, пока не разрешили")
        async with SessionLocal() as s:
            chk(await ov.can_watch(s, u_boss, solo_id) is False,
                "чужая лента закрыта без согласия")
            # Ровно та дыра, ради которой вернули подтверждение: знать ник
            # было достаточно, чтобы читать чужой склад.
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            await expect_403(
                admin.resolve_scope(store_id=solo_id, user=boss, session=s),
                "и панель чужого склада не открывается",
            )

        print("\n[3b] Владелец разрешает — доступ появляется")
        async with SessionLocal() as s:
            solo = (await s.execute(select(User).where(User.id == u_solo))).scalar_one()
            waiting = await ov.pending_for(s, solo)
            chk(len(waiting) == 1, "запрос виден адресату", str(len(waiting)))
            store = await ov.bind(s, waiting[0], solo)
            await s.commit()
        chk(store is not None and store.name == "OV-SOLO", "склад привязан после согласия")
        async with SessionLocal() as s:
            chk(await ov.can_watch(s, u_boss, solo_id) is True, "can_watch=True")

        print("\n[3c] Отказ закрывает запрос насовсем")
        async with SessionLocal() as s:
            other = User(telegram_id=TG_DENY, username="ov_deny", first_name="Отказ")
            s.add(other)
            await s.flush()
            deny_store = Store(name="OV-DENY", owner_id=other.id)
            s.add(deny_store)
            await s.flush()
            s.add(StoreMember(user_id=other.id, store_id=deny_store.id, role=Role.OWNER))
            other.current_store_id = deny_store.id
            await s.commit()
            deny_store_id, deny_user_id = deny_store.id, other.id
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            d = await admin.request_oversight(
                payload=OversightRequest(username="ov_deny"), user=boss, session=s
            )
        async with SessionLocal() as s:
            other = (await s.execute(select(User).where(User.id == deny_user_id))).scalar_one()
            req = (await s.execute(
                select(StoreOversight).where(StoreOversight.id == d.id))).scalar_one()
            await ov.decline(s, req)
            await s.commit()
        async with SessionLocal() as s:
            chk(await ov.can_watch(s, u_boss, deny_store_id) is False,
                "после отказа доступа нет")

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

        print("\n[5] Незнакомое имя ждёт первого /start")
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            later = await admin.request_oversight(
                payload=OversightRequest(username="ov_later"), user=boss, session=s
            )
        chk(later.status == "PENDING", "статус PENDING", later.status)
        chk(later.store_id is None, "склада ещё нет — привязывать не к чему")

        async with SessionLocal() as s:
            newbie = User(telegram_id=999925, username="ov_later", first_name="Поздний")
            s.add(newbie)
            await s.flush()
            # Без склада привязать не к чему — как при /start до его создания.
            waiting = await ov.pending_for(s, newbie)
            chk(len(waiting) == 1, "запрос дождался появления человека", str(len(waiting)))
            chk(await ov.bind(s, waiting[0], newbie) is None,
                "без склада не привязывается")

            late_store = Store(name="OV-LATER", owner_id=newbie.id)
            s.add(late_store)
            await s.flush()
            s.add(StoreMember(user_id=newbie.id, store_id=late_store.id, role=Role.OWNER))
            newbie.current_store_id = late_store.id
            await s.flush()
            waiting = await ov.pending_for(s, newbie)
            bound = await ov.bind(s, waiting[0], newbie)
            await s.commit()
            late_id = late_store.id
        chk(bound is not None, "после создания склада и согласия — привязалось")
        async with SessionLocal() as s:
            chk(await ov.can_watch(s, u_boss, late_id) is True, "поздний склад стал доступен")

        print("\n[6] Режим наблюдения, а не владения")
        async with SessionLocal() as s:
            boss = (await s.execute(select(User).where(User.id == u_boss))).scalar_one()
            sc = await admin.resolve_scope(store_id=solo_id, user=boss, session=s)
        chk(sc.store_id == solo_id, "чужой склад открыт")
        chk(sc.as_owner is False, "но не как владелец")

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
        chk(kinds == {"OV-BOSS": "own", "OV-SOLO": "watch", "OV-LATER": "watch"},
            "в списке свой и оба подключённых", str(kinds))

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
            chk([sc.name for sc in scs] == ["OV-BOSS", "OV-LATER"],
                "отключённый склад пропал, остальные на месте",
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

        print("\n[11] Привязка без склада невозможна")
        async with SessionLocal() as s:
            lone = User(telegram_id=999926, username="ov_lone", first_name="Один")
            s.add(lone)
            await s.flush()
            req3 = StoreOversight(watcher_id=u_boss, target_username="ov_lone")
            s.add(req3)
            await s.flush()
            store = await ov.bind(s, req3, lone)
            await s.rollback()
        chk(store is None, "привязывать не к чему")
    finally:
        async with SessionLocal() as s:
            ALL_STORES = (await s.execute(select(Store.id).where(
                Store.name.in_(("OV-BOSS", "OV-SOLO", "OV-LATER", "OV-DENY"))))).scalars().all()
            await s.execute(delete(StoreOversight).where(
                StoreOversight.watcher_id.in_([u_boss, u_solo, u_emp])))
            await s.execute(delete(AuditLog).where(AuditLog.store_id.in_(ALL_STORES)))
            iids = (await s.execute(select(Item.id).where(
                Item.store_id.in_(ALL_STORES)))).scalars().all()
            await s.execute(delete(ItemStatusLog).where(ItemStatusLog.item_id.in_(iids)))
            await s.execute(delete(Item).where(Item.store_id.in_(ALL_STORES)))
            await s.execute(delete(StoreCounter).where(StoreCounter.store_id.in_(ALL_STORES)))
            await s.execute(delete(StoreMember).where(StoreMember.store_id.in_(ALL_STORES)))
            for tg in (TG_BOSS, TG_SOLO, TG_EMP, TG_LATER, TG_LONE, TG_DENY):
                u = (await s.execute(select(User).where(User.telegram_id == tg))).scalar_one_or_none()
                if u is not None:
                    u.current_store_id = None
            await s.flush()
            await s.execute(delete(Store).where(Store.id.in_(ALL_STORES)))
            await s.execute(delete(User).where(
                User.telegram_id.in_([TG_BOSS, TG_SOLO, TG_EMP, TG_LATER, TG_LONE, TG_DENY])))
            await s.commit()
        print("\n[cleanup] ok")
    print(f"\nИТОГ: {ok} успешно, {bad} провалено")
    sys.exit(1 if bad else 0)


asyncio.run(main())
