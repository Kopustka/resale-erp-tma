"""Длина текстовых полей: понятная ошибка вместо «внутренней ошибки сервера».

Ловится целый класс ошибок, а не один случай. Если у колонки есть предел
длины, а у схемы его нет, слишком длинное значение доходит до базы и падает
там StringDataRightTruncation — наружу уходит 500 без всякой подсказки.
Именно так ломалось добавление вещи с состоянием, написанным словом.
"""
import asyncio, sys
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import String, delete, select

from app.db import SessionLocal
from app.models import Item, ItemStatus, Role, Store, StoreCounter, StoreMember, User
from app.schemas import ItemBase, ItemCreate, ItemUpdate

TG = 999941
ok = bad = 0


def chk(c, n, e=""):
    global ok, bad
    print(("  ✅ " if c else "  ❌ ") + n + ("" if c else f"\n       {e}"))
    if c:
        ok += 1
    else:
        bad += 1


def limit_of(model, field: str):
    """Предел длины, объявленный в схеме pydantic."""
    f = model.model_fields.get(field)
    if f is None:
        return None
    for meta in f.metadata:
        if hasattr(meta, "max_length"):
            return meta.max_length
    return None


async def main():
    global ok, bad
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.telegram_id == TG))).scalar_one_or_none()
        if u is None:
            u = User(telegram_id=TG, username="lim", first_name="Lim")
            s.add(u)
            await s.flush()
        st = Store(name="LIMITS-TEST", owner_id=u.id)
        s.add(st)
        await s.flush()
        s.add(StoreCounter(store_id=st.id))
        s.add(StoreMember(user_id=u.id, store_id=st.id, role=Role.OWNER))
        u.current_store_id = st.id
        await s.commit()
        sid = st.id

    try:
        print("\n[1] Каждая колонка с пределом прикрыта схемой")
        # Ровно та связка, из-за которой ломалось создание вещи.
        gaps = []
        for col in Item.__table__.columns:
            if not isinstance(col.type, String) or col.type.length is None:
                continue
            if col.name in ("sku",):  # генерируется сервером, не приходит извне
                continue
            for schema in (ItemBase, ItemUpdate):
                if col.name not in schema.model_fields:
                    continue
                lim = limit_of(schema, col.name)
                if lim is None or lim > col.type.length:
                    gaps.append(f"{schema.__name__}.{col.name}: предел {lim}, "
                                f"колонка {col.type.length}")
        chk(not gaps, "нет поля, где схема разрешает больше, чем вмещает колонка",
            "\n       ".join(gaps))

        print("\n[2] Состояние словом больше не ломает запись")
        chk(Item.__table__.c.condition.type.length >= 32,
            "колонка condition расширена",
            str(Item.__table__.c.condition.type.length))
        for text in ("Идеальное", "Как новое", "9/10", "Отличное состояние"):
            try:
                ItemCreate(brand="B", category="C", condition=text)
                good = True
            except ValidationError as e:
                good = False
            chk(good, f"схема принимает «{text}»")

        print("\n[3] Значение реально сохраняется в базу")
        async with SessionLocal() as s:
            it = Item(store_id=sid, sku="#L1", title="T", brand="B", category="C",
                      condition="Идеальное состояние", status=ItemStatus.BOUGHT)
            s.add(it)
            await s.commit()
            iid = it.id
        async with SessionLocal() as s:
            fresh = (await s.execute(select(Item).where(Item.id == iid))).scalar_one()
        chk(fresh.condition == "Идеальное состояние", "сохранилось без обрезки",
            str(fresh.condition))

        print("\n[4] Слишком длинное отбивается схемой, а не базой")
        too_long = "О" * 33
        try:
            ItemCreate(brand="B", category="C", condition=too_long)
            chk(False, "33 символа отклонены схемой", "приняла — упадёт уже в базе")
        except ValidationError:
            chk(True, "33 символа отклонены схемой")
        try:
            ItemUpdate(condition=too_long)
            chk(False, "правка длинным значением тоже отклонена")
        except ValidationError:
            chk(True, "правка длинным значением тоже отклонена")

        print("\n[5] Прочие поля с прежними пределами не пострадали")
        cases = [
            ("size", 20), ("color", 40), ("category", 60),
            ("brand", 80), ("title", 100), ("purchase_location", 120),
        ]
        for field, n in cases:
            base = {"brand": "B", "category": "C"}
            base[field] = "x" * n
            try:
                ItemCreate(**base)
                fits = True
            except ValidationError:
                fits = False
            base[field] = "x" * (n + 1)
            try:
                ItemCreate(**base)
                over = True
            except ValidationError:
                over = False
            chk(fits and not over, f"{field}: ровно {n} проходит, {n + 1} — нет",
                f"fits={fits} over={over}")
    finally:
        async with SessionLocal() as s:
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
