"""Настраиваемая форма вещи: набор по умолчанию, правка, свои поля.

Главное, что проверяется: своё поле не может притвориться колонкой вещи и
подменить цену или бренд, а обязательность проверяется на сервере, а не
только в интерфейсе.
"""
import asyncio, sys
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (AuditLog, FieldKind, Item, ItemStatus, Role, Store,
                        StoreCounter, StoreField, StoreMember, User)
from app.routers import fields as router
from app.routers import items as items_router
from app.schemas import FieldCreate, FieldPatch, FieldsUpdate, ItemCreate
from app.services import fields as svc

TG = 999951
ok = bad = 0


def chk(c, n, e=""):
    global ok, bad
    print(("  ✅ " if c else "  ❌ ") + n + ("" if c else f"\n       {e}"))
    if c:
        ok += 1
    else:
        bad += 1


async def main():
    global ok, bad
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.telegram_id == TG))).scalar_one_or_none()
        if u is None:
            u = User(telegram_id=TG, username="fld", first_name="Fld")
            s.add(u)
            await s.flush()
        st = Store(name="FIELDS-TEST", owner_id=u.id, base_currency="BYN")
        s.add(st)
        await s.flush()
        s.add(StoreCounter(store_id=st.id))
        m = StoreMember(user_id=u.id, store_id=st.id, role=Role.OWNER)
        s.add(m)
        u.current_store_id = st.id
        await s.commit()
        sid, uid = st.id, u.id

    try:
        print("\n[1] Базовый набор создаётся сам")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(StoreMember.store_id == sid))).scalar_one()
            rows = await router.list_fields(member=mm, session=s)
        keys = [f.key for f in rows]
        chk(len(rows) == len(svc.DEFAULTS), f"полей {len(svc.DEFAULTS)}", str(len(rows)))
        chk(keys == [k for k, *_ in svc.DEFAULTS], "порядок как в наборе по умолчанию")
        chk(all(f.builtin for f in rows), "все помечены встроенными")
        chk(sum(1 for f in rows if f.enabled) >= 14, "почти все включены")

        print("\n[2] Повторный вызов не плодит дубли")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(StoreMember.store_id == sid))).scalar_one()
            again = await router.list_fields(member=mm, session=s)
        chk(len(again) == len(rows), "количество не изменилось", f"{len(again)} против {len(rows)}")

        print("\n[3] Бренд и категорию не спрятать")
        brand = next(f for f in rows if f.key == "brand")
        chk(brand.locked, "бренд помечен как обязательный навсегда")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(StoreMember.store_id == sid))).scalar_one()
            try:
                await router.update_fields(
                    payload=FieldsUpdate(fields=[FieldPatch(id=brand.id, enabled=False)]),
                    member=mm, session=s)
                chk(False, "скрыть бренд запрещено")
            except HTTPException as e:
                chk(e.status_code == 422, "скрыть бренд запрещено (422)", str(e.status_code))

        print("\n[4] Переименование, порядок и видимость сохраняются")
        color = next(f for f in rows if f.key == "color")
        sleeve = next(f for f in rows if f.key == "sleeve_cm")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(StoreMember.store_id == sid))).scalar_one()
            upd = await router.update_fields(
                payload=FieldsUpdate(fields=[
                    FieldPatch(id=color.id, label="Расцветка", required=True, position=0),
                    FieldPatch(id=sleeve.id, enabled=False),
                ]),
                member=mm, session=s)
        by = {f.key: f for f in upd}
        chk(by["color"].label == "Расцветка", "название изменилось", by["color"].label)
        chk(by["color"].required is True, "стало обязательным")
        chk(by["color"].position == 0 and upd[0].key == "color", "уехало наверх", upd[0].key)
        chk(by["sleeve_cm"].enabled is False, "рукав скрыт")

        print("\n[5] Своё поле заводится и получает свой ключ")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(StoreMember.store_id == sid))).scalar_one()
            lot = await router.create_field(
                payload=FieldCreate(label="Номер лота", kind="TEXT", required=True,
                                    hint="как в записной книжке"),
                member=mm, session=s)
            sel = await router.create_field(
                payload=FieldCreate(label="Сезон", kind="SELECT", options=["Лето", "Зима"]),
                member=mm, session=s)
        chk(lot.builtin is False, "поле помечено своим")
        chk(lot.key and lot.key not in svc.BUILTIN_KEYS, "ключ не совпал со встроенным", lot.key)
        chk(lot.hint == "как в записной книжке", "подсказка сохранена")
        chk(sel.options == ["Лето", "Зима"], "варианты сохранены", str(sel.options))

        print("\n[6] Поле с выбором без вариантов не заводится")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(StoreMember.store_id == sid))).scalar_one()
            try:
                await router.create_field(
                    payload=FieldCreate(label="Пустой выбор", kind="SELECT", options=[]),
                    member=mm, session=s)
                chk(False, "выбор без вариантов отклонён")
            except HTTPException as e:
                chk(e.status_code == 422, "выбор без вариантов отклонён (422)", str(e.status_code))

        print("\n[7] Своё поле не подменяет колонку вещи")
        # Ключ вида «brand» для своего поля недопустим — иначе значение из
        # extra перезаписало бы бренд при создании.
        taken = {f.key for f in upd}
        chk(svc.make_key("Бренд", taken) not in svc.BUILTIN_KEYS,
            "ключ из слова «Бренд» не станет колонкой", svc.make_key("Бренд", taken))
        fake = [type("F", (), {"key": lot.key, "builtin": False})()]
        cols, extra = svc.split_payload(
            {"brand": "Nike", "title": "Худи", lot.key: "A-17", "чужое": "x"}, fake)
        chk(cols.get("brand") == "Nike", "колонка осталась колонкой")
        chk(extra == {lot.key: "A-17"}, "в свои попало только своё", str(extra))
        chk("чужое" not in cols, "необъявленный ключ убран из колонок", str(cols))
        chk(svc.filter_extra({lot.key: "A", "левое": "B"}, fake) == {lot.key: "A"},
            "необъявленное значение отсеяно")

        print("\n[8] Обязательное своё поле проверяется на сервере")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(StoreMember.store_id == sid))).scalar_one()
            uu = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            try:
                await items_router.create_item(
                    payload=ItemCreate(brand="Nike", category="Худи", title="",
                                       color="Чёрный", cost_price=Decimal(10)),
                    member=mm, user=uu, session=s)
                chk(False, "без обязательного поля вещь не создаётся")
            except HTTPException as e:
                chk(e.status_code == 422 and "Номер лота" in str(e.detail),
                    "сказано, какое поле заполнить", str(e.detail)[:90])

        print("\n[9] Со значением вещь создаётся, значение сохраняется")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(StoreMember.store_id == sid))).scalar_one()
            uu = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            out = await items_router.create_item(
                payload=ItemCreate(brand="Nike", category="Худи", title="Тест",
                                   color="Чёрный", cost_price=Decimal(10),
                                   extra={lot.key: "A-17", "чужое": "мусор"}),
                member=mm, user=uu, session=s)
            iid = out.id
        chk(out.extra.get(lot.key) == "A-17", "своё значение вернулось", str(out.extra))
        chk("чужое" not in out.extra, "необъявленное не сохранилось", str(out.extra))
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == iid))).scalar_one()
        chk(it.extra.get(lot.key) == "A-17", "и лежит в базе", str(it.extra))
        chk(it.brand == "Nike", "бренд не пострадал", it.brand)

        print("\n[10] Встроенное поле не удалить, своё — да")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(StoreMember.store_id == sid))).scalar_one()
            try:
                await router.delete_field(field_id=brand.id, member=mm, session=s)
                chk(False, "встроенное удалить нельзя")
            except HTTPException as e:
                chk(e.status_code == 422, "встроенное удалить нельзя (422)", str(e.status_code))
            await router.delete_field(field_id=sel.id, member=mm, session=s)
        async with SessionLocal() as s:
            left = (await s.execute(select(StoreField.key).where(
                StoreField.store_id == sid))).scalars().all()
        chk(sel.key not in left, "своё поле удалено")
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == iid))).scalar_one()
        chk(it.extra.get(lot.key) == "A-17", "значения других полей на месте")
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(AuditLog).where(AuditLog.store_id == sid))
            await s.execute(delete(StoreField).where(StoreField.store_id == sid))
            from app.models import ItemStatusLog
            iids = (await s.execute(select(Item.id).where(Item.store_id == sid))).scalars().all()
            await s.execute(delete(ItemStatusLog).where(ItemStatusLog.item_id.in_(iids)))
            await s.execute(delete(Item).where(Item.store_id == sid))
            await s.execute(delete(StoreCounter).where(StoreCounter.store_id == sid))
            await s.execute(delete(StoreMember).where(StoreMember.store_id == sid))
            uu = (await s.execute(select(User).where(User.telegram_id == TG))).scalar_one_or_none()
            if uu is not None:
                uu.current_store_id = None
            await s.flush()
            await s.execute(delete(Store).where(Store.id == sid))
            await s.execute(delete(User).where(User.telegram_id == TG))
            await s.commit()
        print("\n[cleanup] ok")
    print(f"\nИТОГ: {ok} успешно, {bad} провалено")
    sys.exit(1 if bad else 0)


asyncio.run(main())
