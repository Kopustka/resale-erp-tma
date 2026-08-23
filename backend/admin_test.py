"""Админ-панель владельца: доступ, лента действий, сводка по команде.

Тест неразрушающий: поднимает собственный склад с владельцем и сотрудником,
проверяет и в конце убирает за собой. Роутер вызывается напрямую, минуя
FastAPI, — так проверяется сама логика; отдельно проверяется, что зависимость
require_role не пускает никого, кроме OWNER.
"""
import asyncio, sys, uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.auth import OWNER_ONLY, require_role
from app.db import SessionLocal
from app.models import (AuditLog, Discount, DiscountStatus, InviteStatus, Item,
                        ItemStatus, ItemStatusLog, Role, Store, StoreCounter,
                        StoreInvite, StoreMember, User)
from app.routers import admin
from app.services import audit

TG_OWNER, TG_EMP, TG_ANA = 999911, 999912, 999913
ok = bad = 0


def chk(c, n, e=""):
    global ok, bad
    print(("  ✅ " if c else "  ❌ ") + n + ("" if c else f"\n       {e}"))
    if c:
        ok += 1
    else:
        bad += 1


async def call_dep(dep, member):
    """Прогнать зависимость require_role с конкретным членством."""
    return await dep(member=member)


async def main():
    global ok, bad
    async with SessionLocal() as s:
        users = {}
        for tg, uname in ((TG_OWNER, "adm_owner"), (TG_EMP, "adm_emp"), (TG_ANA, "adm_ana")):
            u = (await s.execute(select(User).where(User.telegram_id == tg))).scalar_one_or_none()
            if u is None:
                u = User(telegram_id=tg, username=uname, first_name=uname)
                s.add(u)
                await s.flush()
            users[tg] = u
        st = Store(name="ADMIN-TEST", owner_id=users[TG_OWNER].id)
        s.add(st)
        await s.flush()
        s.add(StoreCounter(store_id=st.id))
        members = {}
        for tg, role in ((TG_OWNER, Role.OWNER), (TG_EMP, Role.EMPLOYEE), (TG_ANA, Role.ANALYST)):
            m = StoreMember(user_id=users[tg].id, store_id=st.id, role=role)
            s.add(m)
            members[tg] = m
            users[tg].current_store_id = st.id
        await s.flush()

        # Сотрудник добавил вещь, провёл её по статусам и сделал скидку.
        it = Item(store_id=st.id, sku="#A1", title="Куртка", brand="N", category="C",
                  status=ItemStatus.LISTED, purchaser_id=users[TG_EMP].id,
                  list_price_orig=1000, price_currency="RUB")
        s.add(it)
        await s.flush()
        for old, new in ((None, ItemStatus.BOUGHT), (ItemStatus.BOUGHT, ItemStatus.PHOTOGRAPHED),
                         (ItemStatus.PHOTOGRAPHED, ItemStatus.LISTED)):
            s.add(ItemStatusLog(item_id=it.id, old_status=old, new_status=new,
                                changed_by=users[TG_EMP].id))
        s.add(Discount(store_id=st.id, item_id=it.id, old_price=1000, new_price=800,
                       currency="RUB", status=DiscountStatus.SCHEDULED,
                       created_by=users[TG_EMP].id))
        audit.record(s, store_id=st.id, user_id=users[TG_EMP].id,
                     action=audit.ITEM_CREATE, summary="#A1 Куртка",
                     entity_type="item", entity_id=it.id)
        audit.record(s, store_id=st.id, user_id=users[TG_OWNER].id,
                     action=audit.SETTINGS_EDIT, summary="базовая валюта → RUB")
        s.add(StoreInvite(store_id=st.id, username="pending_guy", role=Role.EMPLOYEE,
                          invited_by=users[TG_OWNER].id, status=InviteStatus.PENDING))
        await s.commit()
        sid = st.id
        uid_owner, uid_emp = users[TG_OWNER].id, users[TG_EMP].id
        m_owner, m_emp, m_ana = members[TG_OWNER], members[TG_EMP], members[TG_ANA]

    try:
        print("\n[1] Доступ только у владельца")
        dep = require_role(*OWNER_ONLY)
        got = await call_dep(dep, m_owner)
        chk(got is m_owner, "OWNER проходит")
        for m, name in ((m_emp, "EMPLOYEE"), (m_ana, "ANALYST")):
            try:
                await call_dep(dep, m)
                chk(False, f"{name} получает 403")
            except HTTPException as e:
                chk(e.status_code == 403, f"{name} получает 403", str(e.status_code))

        print("\n[2] Лента: журнал + статусы в одном потоке")
        async with SessionLocal() as s:
            page = await admin.activity(member=m_owner, session=s, user_id=None, group=None,
                                        days=30, limit=50, offset=0)
        acts = [e.action for e in page.events]
        chk(len(page.events) == 5, "5 событий (2 журнал + 3 статуса)", str(acts))
        chk(acts.count("status") == 3, "смены статуса попали в ленту", str(acts))
        chk(audit.ITEM_CREATE in acts, "добавление вещи попало в ленту", str(acts))
        chk(all(e.at is not None for e in page.events), "у всех событий есть время")
        times = [e.at for e in page.events]
        chk(times == sorted(times, reverse=True), "новое сверху", str(times))
        chk(all(e.icon and e.title for e in page.events), "у всех есть значок и подпись")

        print("\n[3] Автор события подписан именем")
        by_action = {e.action: e for e in page.events}
        emp_ev = by_action[audit.ITEM_CREATE]
        chk(emp_ev.actor.username == "adm_emp", "видно, кто сделал", str(emp_ev.actor))
        chk(emp_ev.actor.role == Role.EMPLOYEE, "и его роль", str(emp_ev.actor.role))
        chk(by_action[audit.SETTINGS_EDIT].actor.username == "adm_owner",
            "действие владельца подписано им")

        print("\n[4] Фильтр по участнику")
        async with SessionLocal() as s:
            only_emp = await admin.activity(member=m_owner, session=s, user_id=uid_emp,
                                            group=None, days=30, limit=50, offset=0)
            only_own = await admin.activity(member=m_owner, session=s, user_id=uid_owner,
                                            group=None, days=30, limit=50, offset=0)
        chk(len(only_emp.events) == 4, "у сотрудника 4 события", str(len(only_emp.events)))
        chk(all(e.actor.user_id == uid_emp for e in only_emp.events), "чужого не подмешалось")
        chk(len(only_own.events) == 1, "у владельца 1 событие", str(len(only_own.events)))

        print("\n[5] Фильтр по группе")
        async with SessionLocal() as s:
            g_status = await admin.activity(member=m_owner, session=s, user_id=None,
                                            group="status", days=30, limit=50, offset=0)
            g_items = await admin.activity(member=m_owner, session=s, user_id=None,
                                           group="items", days=30, limit=50, offset=0)
            g_set = await admin.activity(member=m_owner, session=s, user_id=None,
                                         group="settings", days=30, limit=50, offset=0)
        chk(len(g_status.events) == 3 and {e.action for e in g_status.events} == {"status"},
            "«Статусы» отдают только статусы", str(len(g_status.events)))
        chk([e.action for e in g_items.events] == [audit.ITEM_CREATE],
            "«Вещи» отдают только вещи", str([e.action for e in g_items.events]))
        chk([e.action for e in g_set.events] == [audit.SETTINGS_EDIT],
            "«Настройки» отдают только настройки", str([e.action for e in g_set.events]))

        print("\n[6] Постраничность")
        async with SessionLocal() as s:
            p1 = await admin.activity(member=m_owner, session=s, user_id=None, group=None,
                                      days=30, limit=2, offset=0)
            p2 = await admin.activity(member=m_owner, session=s, user_id=None, group=None,
                                      days=30, limit=2, offset=2)
            p3 = await admin.activity(member=m_owner, session=s, user_id=None, group=None,
                                      days=30, limit=2, offset=4)
        chk(len(p1.events) == 2 and p1.has_more, "первая страница + есть ещё")
        chk(len(p3.events) == 1 and not p3.has_more, "последняя страница без продолжения",
            f"{len(p3.events)} has_more={p3.has_more}")
        ids = [e.id for e in p1.events + p2.events + p3.events]
        chk(len(set(ids)) == 5, "страницы не дублируют события", str(ids))

        print("\n[7] Окно по дням отсекает старое")
        async with SessionLocal() as s:
            old = AuditLog(store_id=sid, user_id=uid_emp, action=audit.ITEM_EDIT,
                           summary="давно", created_at=datetime.now(timezone.utc) - timedelta(days=90))
            s.add(old)
            await s.commit()
            old_id = old.id
        async with SessionLocal() as s:
            recent = await admin.activity(member=m_owner, session=s, user_id=None, group=None,
                                          days=30, limit=50, offset=0)
            far = await admin.activity(member=m_owner, session=s, user_id=None, group=None,
                                       days=180, limit=50, offset=0)
        chk(len(recent.events) == 5, "за 30 дней старое не видно", str(len(recent.events)))
        chk(len(far.events) == 6, "за 180 дней видно", str(len(far.events)))

        print("\n[8] Сводка по команде")
        async with SessionLocal() as s:
            ov = await admin.team(member=m_owner, session=s, days=30)
        by_user = {m.username: m for m in ov.members}
        chk(len(ov.members) == 3, "все три участника в сводке", str(list(by_user)))
        chk(ov.members[0].role == Role.OWNER, "владелец первым в списке")
        emp = by_user["adm_emp"]
        chk(emp.items_added == 1, "добавленная вещь засчитана", str(emp.items_added))
        chk(emp.listed == 1, "выставление засчитано", str(emp.listed))
        chk(emp.shipped == 0, "отправок нет", str(emp.shipped))
        chk(emp.discounts == 1, "скидка засчитана", str(emp.discounts))
        chk(emp.operations == 4, "всего 4 операции (1 журнал + 3 статуса)", str(emp.operations))
        chk(emp.last_action_at is not None, "видно время последней активности")
        chk(by_user["adm_owner"].operations == 1, "у владельца 1 операция",
            str(by_user["adm_owner"].operations))
        chk(by_user["adm_ana"].operations == 0, "аналитик ничего не делал")
        chk([i.username for i in ov.invites] == ["pending_guy"], "непринятое приглашение видно",
            str([i.username for i in ov.invites]))

        print("\n[9] Чужой склад не протекает")
        async with SessionLocal() as s:
            other = Store(name="ADMIN-OTHER", owner_id=uid_owner)
            s.add(other)
            await s.flush()
            other_m = StoreMember(user_id=uid_owner, store_id=other.id, role=Role.OWNER)
            s.add(other_m)
            audit.record(s, store_id=other.id, user_id=uid_owner,
                         action=audit.ITEM_CREATE, summary="чужая вещь")
            await s.commit()
            other_id = other.id
            foreign = await admin.activity(member=other_m, session=s, user_id=None, group=None,
                                           days=30, limit=50, offset=0)
            mine = await admin.activity(member=m_owner, session=s, user_id=None, group=None,
                                        days=30, limit=50, offset=0)
        chk(len(foreign.events) == 1, "во втором складе только его событие",
            str(len(foreign.events)))
        chk(all("чужая вещь" not in e.summary for e in mine.events),
            "в первом складе чужого нет")

        print("\n[10] Подписи действий заведены для всех кодов")
        codes = [v for k, v in vars(audit).items()
                 if k.isupper() and isinstance(v, str) and "." in v]
        missing = [c for c in codes if c not in audit.LABELS]
        chk(not missing, "у каждого кода есть подпись", str(missing))
        groups = {audit.group_of(c) for c in codes}
        chk(groups <= set(audit.GROUPS), "все группы известны", str(groups - set(audit.GROUPS)))
        chk(audit.group_of("status") == "status", "статусы — своя группа")
        chk(audit.label_of("несуществующее")[1] == "несуществующее",
            "неизвестный код не теряется")
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(AuditLog).where(AuditLog.store_id.in_([sid, other_id])))
            await s.execute(delete(StoreInvite).where(StoreInvite.store_id == sid))
            await s.execute(delete(Discount).where(Discount.store_id == sid))
            items = (await s.execute(select(Item.id).where(Item.store_id == sid))).scalars().all()
            await s.execute(delete(ItemStatusLog).where(ItemStatusLog.item_id.in_(items)))
            await s.execute(delete(Item).where(Item.store_id == sid))
            await s.execute(delete(StoreCounter).where(StoreCounter.store_id == sid))
            await s.execute(delete(StoreMember).where(StoreMember.store_id.in_([sid, other_id])))
            for tg in (TG_OWNER, TG_EMP, TG_ANA):
                u = (await s.execute(select(User).where(User.telegram_id == tg))).scalar_one()
                u.current_store_id = None
            await s.flush()
            await s.execute(delete(Store).where(Store.id.in_([sid, other_id])))
            await s.execute(delete(User).where(User.telegram_id.in_([TG_OWNER, TG_EMP, TG_ANA])))
            await s.commit()
        print("\n[cleanup] ok")
    print(f"\nИТОГ: {ok} успешно, {bad} провалено")
    sys.exit(1 if bad else 0)


asyncio.run(main())
