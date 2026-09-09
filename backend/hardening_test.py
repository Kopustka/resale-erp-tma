"""Закрепляет то, что чинилось после общего разбора кода.

Каждая проверка соответствует найденному дефекту. Роутеры вызываются
напрямую, без FastAPI: проверяется логика, а не маршрутизация. Тест
неразрушающий — поднимает свой склад и в конце убирает за собой.

Запуск: env -u BOT_TOKEN python hardening_test.py
"""
import asyncio
import sys
import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import delete, select, text

from app.db import SessionLocal
from app.models import (
    AuditLog,
    Item,
    ItemPost,
    ItemStatus,
    ItemStatusLog,
    Channel,
    FieldKind,
    Role,
    Store,
    StoreCounter,
    StoreField,
    StoreInvite,
    StoreMember,
    User,
)
from app.routers import items as items_router
from app.routers import stores as stores_router
from app.schemas import ItemCreate, ItemUpdate
from app.services import fields as fields_svc

TG_OWNER, TG_EMP = 999941, 999942
STORE_NAME = "HARDENING-TEST"

ok = bad = 0


def chk(cond: bool, what: str, got: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        print(f"  ✅ {what}")
    else:
        bad += 1
        print(f"  ❌ {what}" + (f"\n       получено: {got}" if got else ""))


async def expect_status(coro, code: int, what: str) -> None:
    """Ждём именно HTTPException с этим кодом, а не любую ошибку."""
    try:
        await coro
        chk(False, what, "исключения не было")
    except HTTPException as e:
        chk(e.status_code == code, what, f"код {e.status_code}: {e.detail}")


async def main() -> None:
    async with SessionLocal() as s:
        owner = User(telegram_id=TG_OWNER, username="hard_owner", first_name="Влад")
        emp = User(telegram_id=TG_EMP, username="hard_emp", first_name="Сотр")
        s.add_all([owner, emp])
        await s.flush()
        store = Store(name=STORE_NAME, owner_id=owner.id, base_currency="BYN")
        s.add(store)
        await s.flush()
        m_owner = StoreMember(user_id=owner.id, store_id=store.id, role=Role.OWNER)
        m_emp = StoreMember(user_id=emp.id, store_id=store.id, role=Role.EMPLOYEE)
        s.add_all([m_owner, m_emp])
        owner.current_store_id = emp.current_store_id = store.id
        await s.commit()
        store_id, owner_id, emp_id = store.id, owner.id, emp.id

    try:
        # ---------------------------------------------------------------- #
        print("\n[1] Своё поле с деньгами не обходит разделение ролей")
        async with SessionLocal() as s:
            await fields_svc.ensure_defaults(s, store_id)
            s.add_all([
                StoreField(store_id=store_id, key="courier_paid", label="Отдал курьеру",
                           kind=FieldKind.MONEY, position=90, builtin=False, enabled=True),
                StoreField(store_id=store_id, key="lot_no", label="Номер лота",
                           kind=FieldKind.TEXT, position=91, builtin=False, enabled=True),
            ])
            await s.commit()

        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            u = (await s.execute(select(User).where(User.id == owner_id))).scalar_one()
            created = await items_router.create_item(
                payload=ItemCreate(
                    brand="Nike", category="Куртки", title="Тестовая",
                    cost_price=Decimal("100"), cost_currency="USD",
                    list_price=Decimal("300"), price_currency="USD",
                    extra={"courier_paid": "55", "lot_no": "A-17"},
                ),
                member=mo, session=s, user=u,
            )
            item_id = created.id
        chk(created.extra.get("courier_paid") == "55", "владелец видит своё денежное поле")

        async with SessionLocal() as s:
            me = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == emp_id, StoreMember.store_id == store_id))).scalar_one()
            seen = await items_router.get_item(item_id=item_id, member=me, session=s)
        chk("courier_paid" not in seen.extra,
            "сотрудник не видит денежное своё поле", str(seen.extra))
        chk(seen.extra.get("lot_no") == "A-17", "а обычное своё поле видит")
        chk(seen.cost_price is None, "закупочная цена по-прежнему скрыта")

        async with SessionLocal() as s:
            me = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == emp_id, StoreMember.store_id == store_id))).scalar_one()
            await items_router.edit_item(
                item_id=item_id, payload=ItemUpdate(extra={"courier_paid": "999", "lot_no": "B-2"}),
                member=me, session=s,
            )
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == item_id))).scalar_one()
        chk(it.extra.get("courier_paid") == "55", "и записать в него не может", str(it.extra))
        chk(it.extra.get("lot_no") == "B-2", "обычное поле при этом сохранилось")

        # ---------------------------------------------------------------- #
        print("\n[2] Правка одних только своих полей сохраняется")
        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            await items_router.edit_item(
                item_id=item_id, payload=ItemUpdate(extra={"lot_no": "C-9"}),
                member=mo, session=s,
            )
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == item_id))).scalar_one()
        chk(it.extra.get("lot_no") == "C-9",
            "ответ 200 больше не расходится с базой", str(it.extra))

        # ---------------------------------------------------------------- #
        print("\n[3] Правка одной суммы не переоценивает остальные")
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == item_id))).scalar_one()
            cost_before, list_before = it.cost_price, it.list_price
            version_now = it.version
        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            await items_router.edit_item(
                item_id=item_id, payload=ItemUpdate(platform_fee=Decimal("7")),
                member=mo, session=s,
            )
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == item_id))).scalar_one()
        chk(it.cost_price == cost_before,
            "закупка в базовой валюте не тронута", f"{cost_before} → {it.cost_price}")
        chk(it.list_price == list_before,
            "цена в объявлении не тронута", f"{list_before} → {it.list_price}")
        chk(it.platform_fee_orig == Decimal("7"), "а комиссия записалась")

        # ---------------------------------------------------------------- #
        print("\n[4] Версия защищает от затирания чужой правки")
        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            await expect_status(
                items_router.edit_item(
                    item_id=item_id,
                    payload=ItemUpdate(title="Из устаревшей формы", version=version_now - 5),
                    member=mo, session=s,
                ),
                409, "устаревшая версия отбита (409)",
            )
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == item_id))).scalar_one()
            fresh_version = it.version
        chk(it.title != "Из устаревшей формы", "название не затёрто")
        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            out = await items_router.edit_item(
                item_id=item_id, payload=ItemUpdate(title="С верной версией", version=fresh_version),
                member=mo, session=s,
            )
        chk(out.title == "С верной версией", "с верной версией правка проходит")

        # ---------------------------------------------------------------- #
        print("\n[5] Дату закупки можно указать своей")
        from datetime import datetime, timedelta, timezone
        long_ago = datetime.now(timezone.utc) - timedelta(days=400)
        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            u = (await s.execute(select(User).where(User.id == owner_id))).scalar_one()
            old = await items_router.create_item(
                payload=ItemCreate(brand="Old", category="Обувь", title="Давняя",
                                   purchase_date=long_ago),
                member=mo, session=s, user=u,
            )
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == old.id))).scalar_one()
        chk(abs((it.purchase_date - long_ago).total_seconds()) < 5,
            "дата закупки не подменяется днём заведения", str(it.purchase_date))

        # ---------------------------------------------------------------- #
        print("\n[6] Безвозвратно удаляет только владелец — и удаляет успешно")
        async with SessionLocal() as s:
            # Публикация: именно на ней удаление падало внешним ключом.
            ch = Channel(store_id=store_id, chat_id="-100777000111", enabled=True)
            s.add(ch)
            await s.flush()
            s.add(ItemPost(item_id=item_id, channel_id=ch.id, message_id=1, sold_marked=False))
            await s.commit()
            channel_id = ch.id

        async with SessionLocal() as s:
            me = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == emp_id, StoreMember.store_id == store_id))).scalar_one()
            await expect_status(
                items_router.delete_item(item_id=item_id, hard=True, member=me, session=s),
                403, "сотруднику безвозвратное удаление запрещено",
            )
        async with SessionLocal() as s:
            me = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == emp_id, StoreMember.store_id == store_id))).scalar_one()
            await items_router.delete_item(item_id=item_id, hard=False, member=me, session=s)
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == item_id))).scalar_one()
        chk(it.archived_at is not None, "в архив сотрудник убрать по-прежнему может")

        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            await items_router.delete_item(item_id=item_id, hard=True, member=mo, session=s)
        async with SessionLocal() as s:
            gone = (await s.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()
            posts = (await s.execute(
                select(ItemPost).where(ItemPost.item_id == item_id))).scalars().all()
        chk(gone is None, "вещь с публикацией удалилась (раньше был 500 по внешнему ключу)")
        chk(not posts, "и запись публикации ушла вместе с ней")

        # ---------------------------------------------------------------- #
        print("\n[7] Участника можно исключить")
        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            u = (await s.execute(select(User).where(User.id == owner_id))).scalar_one()
            await expect_status(
                stores_router.remove_member(user_id=owner_id, member=mo, user=u, session=s),
                422, "себя исключить нельзя",
            )
            await stores_router.remove_member(user_id=emp_id, member=mo, user=u, session=s)
        async with SessionLocal() as s:
            left = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == emp_id, StoreMember.store_id == store_id))).scalar_one_or_none()
            gone_user = (await s.execute(select(User).where(User.id == emp_id))).scalar_one()
        chk(left is None, "членство удалено")
        chk(gone_user.current_store_id is None, "исключённый больше не «стоит» на этом складе")

        # ---------------------------------------------------------------- #
        print("\n[8] Поиск и фильтры")
        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            u = (await s.execute(select(User).where(User.id == owner_id))).scalar_one()
            for brand, cat in (("Zara", "Платья"), ("100% Cotton", "Футболки")):
                await items_router.create_item(
                    payload=ItemCreate(brand=brand, category=cat, title=f"{brand} {cat}"),
                    member=mo, session=s, user=u,
                )

        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            page = await items_router.list_items(
                cursor=None, limit=30, status_filter=None, brand=None,
                category="Платья", search=None, ids=None, archived=False,
                member=mo, session=s,
            )
            cats = {i.category for i in page.items}
        chk(cats == {"Платья"}, "фильтр по категории работает", str(cats))

        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            page = await items_router.list_items(
                cursor=None, limit=30, status_filter=None, brand=None, category=None,
                search="100%", ids=None, archived=False, member=mo, session=s,
            )
        chk(len(page.items) == 1 and page.items[0].brand == "100% Cotton",
            "процент в поиске ищется буквально, а не как «что угодно»",
            f"нашлось {len(page.items)}")

        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            await expect_status(
                items_router.list_items(
                    cursor="не-курсор", limit=30, status_filter=None, brand=None,
                    category=None, search=None, ids=None, archived=False,
                    member=mo, session=s,
                ),
                422, "битый курсор — понятный отказ, а не 500",
            )

        # ---------------------------------------------------------------- #
        print("\n[9] Смена базовой валюты пересчитывает уже заведённое")
        # Вещь с суммами: без неё пересчитывать нечего и проверка пуста.
        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            u = (await s.execute(select(User).where(User.id == owner_id))).scalar_one()
            await items_router.create_item(
                payload=ItemCreate(brand="Money", category="Пальто", title="С деньгами",
                                   cost_price=Decimal("200"), cost_currency="BYN",
                                   list_price=Decimal("500"), price_currency="BYN"),
                member=mo, session=s, user=u,
            )
        async with SessionLocal() as s:
            rows = (await s.execute(
                select(Item.cost_price, Item.list_price).where(Item.store_id == store_id)
            )).all()
            before = [(a, b) for a, b in rows]
        async with SessionLocal() as s:
            mo = (await s.execute(select(StoreMember).where(
                StoreMember.user_id == owner_id, StoreMember.store_id == store_id))).scalar_one()
            from app.schemas import StoreSettings
            await stores_router.set_settings(
                payload=StoreSettings(base_currency="USD"), member=mo, session=s,
            )
        async with SessionLocal() as s:
            rows = (await s.execute(
                select(Item.cost_price, Item.list_price).where(Item.store_id == store_id)
            )).all()
            after = [(a, b) for a, b in rows]
        changed = any(
            (b[0] or 0) != (a[0] or 0) or (b[1] or 0) != (a[1] or 0)
            for a, b in zip(before, after) if (a[0] or a[1])
        )
        chk(changed, "суммы переведены в новую базовую валюту")

        # ---------------------------------------------------------------- #
        print("\n[10] Артикул уникален в пределах склада")
        async with SessionLocal() as s:
            existing = (await s.execute(
                select(Item.sku).where(Item.store_id == store_id).limit(1))).scalar_one()
            try:
                await s.execute(text(
                    "INSERT INTO items (id, store_id, sku, title, brand, category, status,"
                    " cost_price, restore_cost, delivery_cost, platform_fee, version,"
                    " cost_price_orig, restore_cost_orig, delivery_cost_orig, platform_fee_orig)"
                    " VALUES (:i, :s, :sku, 't', 'b', 'c', 'BOUGHT', 0,0,0,0,1, 0,0,0,0)"
                ), {"i": uuid.uuid4(), "s": store_id, "sku": existing})
                await s.commit()
                chk(False, "дубль артикула отбит базой")
            except Exception as e:
                await s.rollback()
                chk("uq_item_store_sku" in str(e), "дубль артикула отбит базой", str(e)[:80])

        # ---------------------------------------------------------------- #
        print("\n[11] Сборщик мусора знает ровно те форматы, что принимает загрузка")
        from app.routers.media import ALLOWED_TYPES
        from app.services.media_gc import MEDIA_SUFFIXES
        expected = {f".{e}" for e in ALLOWED_TYPES.values()}
        # Сборщик должен покрывать всё, что кладёт загрузка. Лишнее у него
        # допустимо (.jpeg остался от старых файлов), недостающее — нет:
        # такие файлы копились бы на диске вечно.
        chk(expected <= set(MEDIA_SUFFIXES),
            "сборщик покрывает все принимаемые форматы",
            f"не покрыто: {sorted(expected - set(MEDIA_SUFFIXES))}")

        import tempfile
        from pathlib import Path as _P
        from app.config import get_settings as _gs
        keep = _P(_gs().media_dir) / ".gitkeep"
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.touch()
        import os, time as _t
        os.utime(keep, (0, 0))  # заведомо старый — раньше такой удалялся
        from app.services import media_gc as _gc
        n_before = len(list(_P(_gs().media_dir).iterdir()))
        await _gc.collect_orphans(dry_run=False)
        chk(keep.exists(), "служебный .gitkeep пережил уборку")

    finally:
        async with SessionLocal() as s:
            sids = (await s.execute(
                select(Store.id).where(Store.name == STORE_NAME))).scalars().all()
            iids = (await s.execute(
                select(Item.id).where(Item.store_id.in_(sids)))).scalars().all()
            cids = (await s.execute(
                select(Channel.id).where(Channel.store_id.in_(sids)))).scalars().all()
            await s.execute(delete(ItemPost).where(ItemPost.item_id.in_(iids)))
            await s.execute(delete(ItemPost).where(ItemPost.channel_id.in_(cids)))
            await s.execute(delete(ItemStatusLog).where(ItemStatusLog.item_id.in_(iids)))
            await s.execute(delete(Item).where(Item.store_id.in_(sids)))
            await s.execute(delete(Channel).where(Channel.store_id.in_(sids)))
            await s.execute(delete(AuditLog).where(AuditLog.store_id.in_(sids)))
            await s.execute(delete(StoreField).where(StoreField.store_id.in_(sids)))
            await s.execute(delete(StoreCounter).where(StoreCounter.store_id.in_(sids)))
            await s.execute(delete(StoreInvite).where(StoreInvite.store_id.in_(sids)))
            await s.execute(delete(StoreMember).where(StoreMember.store_id.in_(sids)))
            for tg in (TG_OWNER, TG_EMP):
                u = (await s.execute(
                    select(User).where(User.telegram_id == tg))).scalar_one_or_none()
                if u is not None:
                    u.current_store_id = None
            await s.flush()
            await s.execute(delete(Store).where(Store.id.in_(sids)))
            await s.execute(delete(User).where(User.telegram_id.in_([TG_OWNER, TG_EMP])))
            await s.commit()
        print("\n[cleanup] ok")

    print(f"\nИТОГ: {ok} успешно, {bad} провалено")
    sys.exit(1 if bad else 0)


asyncio.run(main())
