"""Предпросмотр всех публикаций: пост контент-плана, скидка, дроп, вещь.

Проверяется главное свойство: пока владелец не подтвердил, воркер не видит
задание, а подтверждение не публикует немедленно, а лишь снимает блокировку
— запланированное на вечер уходит вечером.
"""
import asyncio, sys, uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import (AuditLog, Channel, CustomPost, CustomPostStatus, Discount,
                        DiscountStatus, Drop, DropItem, Item, ItemPost, ItemStatus,
                        JobKind, JobStatus, PostJob, Role, Store, StoreCounter,
                        StoreMember, User)
from app.routers import discounts as discounts_router
from app.routers import drops as drops_router
from app.routers import posts as posts_router
from app.schemas import CustomPostCreate, DiscountCreate, DropCreate
from app.services import post_queue, preview
from app.bot import _rollback_declined

TG = 999931
ok = bad = 0


def chk(c, n, e=""):
    global ok, bad
    print(("  ✅ " if c else "  ❌ ") + n + ("" if c else f"\n       {e}"))
    if c:
        ok += 1
    else:
        bad += 1


async def jobs_of(s, **flt):
    q = select(PostJob)
    for k, v in flt.items():
        q = q.where(getattr(PostJob, k) == v)
    return list((await s.execute(q)).scalars())


async def main():
    global ok, bad
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.telegram_id == TG))).scalar_one_or_none()
        if u is None:
            u = User(telegram_id=TG, username="prev_all", first_name="Prev")
            s.add(u)
            await s.flush()
        st = Store(name="PREVIEW-ALL", owner_id=u.id, preview_before_post=True,
                   base_currency="RUB")
        s.add(st)
        await s.flush()
        s.add(StoreCounter(store_id=st.id))
        m = StoreMember(user_id=u.id, store_id=st.id, role=Role.OWNER)
        s.add(m)
        u.current_store_id = st.id
        ch = Channel(store_id=st.id, chat_id="@prevall", enabled=True)
        s.add(ch)
        await s.flush()
        it = Item(store_id=st.id, sku="#P1", title="Вещь", brand="B", category="C",
                  status=ItemStatus.LISTED, photo_file_ids=["FID"],
                  list_price_orig=Decimal("1000"), price_currency="RUB",
                  cost_currency="RUB", purchaser_id=u.id)
        s.add(it)
        await s.flush()
        s.add(ItemPost(item_id=it.id, channel_id=ch.id, message_id=555, sold_marked=False))
        await s.commit()
        sid, iid, uid = st.id, it.id, u.id

    try:
        print("\n[1] Разбор кнопки предпросмотра")
        pid = uuid.uuid4()
        chk(preview.parse_callback(f"pub:c:{pid}") == (preview.APPROVE, preview.POST, pid),
            "новый формат с видом сущности")
        chk(preview.parse_callback(f"no:{pid}") == (preview.DECLINE, preview.ITEM, pid),
            "старый двухчастный формат — это вещь")
        chk(preview.parse_callback("pub:z:" + str(pid)) is None, "неизвестный вид отбит")
        chk(preview.parse_callback("pub:c:не-uuid") is None, "мусор отбит")
        chk(preview.parse_callback("") is None, "пустая строка отбита")
        chk(preview.parse_callback(f"pub:b:{pid}") == (preview.APPROVE, preview.BUMP, pid),
            "поднятие — отдельный вид, не путается с вещью")

        print("\n[2] Флаг склада читается")
        async with SessionLocal() as s:
            chk(await preview.is_enabled(s, sid) is True, "предпросмотр включён")

        print("\n[3] Пост контент-плана ждёт подтверждения")
        when = datetime.now(timezone.utc) + timedelta(hours=4)
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(
                StoreMember.store_id == sid))).scalar_one()
            uu = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            out = await posts_router.create_post(
                payload=CustomPostCreate(body="Сегодня в 18:00 большой дроп!",
                                         scheduled_at=when, photo_file_ids=[]),
                user=uu, member=mm, __=mm, session=s,
            )
            post_id = out.id
        async with SessionLocal() as s:
            pj = await jobs_of(s, custom_post_id=post_id)
        chk(len(pj) == 1, "задание создано", str(len(pj)))
        chk(pj[0].status == JobStatus.AWAITING, "и ждёт подтверждения", pj[0].status.value)
        chk(abs((pj[0].run_after - when).total_seconds()) < 2, "время публикации сохранено")

        print("\n[4] Воркер ожидающее задание не берёт")
        async with SessionLocal() as s:
            # Даже если срок наступил — AWAITING невидим для очереди.
            await s.execute(
                PostJob.__table__.update()
                .where(PostJob.id == pj[0].id)
                .values(run_after=datetime.now(timezone.utc) - timedelta(minutes=5))
            )
            await s.commit()
            claimed = await post_queue.claim(s, limit=10)
        chk(all(j.id != pj[0].id for j in claimed), "не попало в выдачу очереди")
        async with SessionLocal() as s:
            fresh = (await s.execute(select(PostJob).where(PostJob.id == pj[0].id))).scalar_one()
        chk(fresh.status == JobStatus.AWAITING, "статус не изменился", fresh.status.value)

        print("\n[5] Подтверждение не ломает расписание")
        async with SessionLocal() as s:
            j = (await s.execute(select(PostJob).where(PostJob.id == pj[0].id))).scalar_one()
            j.run_after = when          # вернём исходный срок
            j.status = JobStatus.PENDING  # то, что делает кнопка «Опубликовать»
            await s.commit()
            claimed = await post_queue.claim(s, limit=10)
        chk(all(x.id != pj[0].id for x in claimed),
            "подтверждённое, но не наступившее — всё ещё ждёт срока")
        async with SessionLocal() as s:
            fresh = (await s.execute(select(PostJob).where(PostJob.id == pj[0].id))).scalar_one()
        chk(fresh.status == JobStatus.PENDING and abs((fresh.run_after - when).total_seconds()) < 2,
            "срок публикации остался прежним")

        print("\n[6] Отказ снимает пост с расписания")
        async with SessionLocal() as s:
            uu = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            note = await _rollback_declined(s, preview.POST, post_id, uu)
            await s.commit()
        async with SessionLocal() as s:
            cp = (await s.execute(select(CustomPost).where(CustomPost.id == post_id))).scalar_one()
        chk(cp.status == CustomPostStatus.CANCELLED, "пост отменён", cp.status.value)
        chk("расписания" in note, "в ответе сказано, что произошло", note)

        print("\n[7] Скидка: задание ждёт, цена применена")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(
                StoreMember.store_id == sid))).scalar_one()
            uu = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            d_out = await discounts_router.create_discount(
                payload=DiscountCreate(item_id=iid, new_price=800, scheduled_at=None),
                member=mm, user=uu, session=s,
            )
            did = d_out.id
        async with SessionLocal() as s:
            dj = await jobs_of(s, discount_id=did)
            item = (await s.execute(select(Item).where(Item.id == iid))).scalar_one()
        chk(len(dj) == 1 and dj[0].status == JobStatus.AWAITING,
            "объявление ждёт подтверждения", str([j.status.value for j in dj]))
        chk(item.list_price_orig == Decimal("800"), "цена уже снижена",
            str(item.list_price_orig))

        print("\n[8] Отказ по скидке возвращает цену")
        async with SessionLocal() as s:
            uu = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            note = await _rollback_declined(s, preview.DISCOUNT, did, uu)
            await s.commit()
        async with SessionLocal() as s:
            item = (await s.execute(select(Item).where(Item.id == iid))).scalar_one()
            d = (await s.execute(select(Discount).where(Discount.id == did))).scalar_one()
        chk(item.list_price_orig == Decimal("1000"), "прежний ценник вернулся",
            str(item.list_price_orig))
        chk(item.price_before_discount is None, "перечёркнутая цена убрана",
            str(item.price_before_discount))
        chk(d.status == DiscountStatus.CANCELLED, "скидка отменена", d.status.value)
        chk("цена возвращена" in note, "в ответе сказано про цену", note)

        print("\n[9] Дроп тоже ждёт подтверждения")
        async with SessionLocal() as s:
            mm = (await s.execute(select(StoreMember).where(
                StoreMember.store_id == sid))).scalar_one()
            uu = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            dr = await drops_router.create_drop(
                payload=DropCreate(item_ids=[iid], title="Тестовый дроп", note=None),
                member=mm, user=uu, session=s,
            )
            drop_id = dr.id
        async with SessionLocal() as s:
            kj = await jobs_of(s, drop_id=drop_id)
        chk(len(kj) == 1 and kj[0].status == JobStatus.AWAITING,
            "дроп ждёт подтверждения", str([j.status.value for j in kj]))

        print("\n[10] Без предпросмотра всё уходит сразу — регрессия")
        async with SessionLocal() as s:
            store = (await s.execute(select(Store).where(Store.id == sid))).scalar_one()
            store.preview_before_post = False
            await s.commit()
            chk(await preview.is_enabled(s, sid) is False, "флаг выключен")
            mm = (await s.execute(select(StoreMember).where(
                StoreMember.store_id == sid))).scalar_one()
            uu = (await s.execute(select(User).where(User.id == uid))).scalar_one()
            out2 = await posts_router.create_post(
                payload=CustomPostCreate(body="Без подтверждения", scheduled_at=None,
                                         photo_file_ids=[]),
                user=uu, member=mm, __=mm, session=s,
            )
        async with SessionLocal() as s:
            pj2 = await jobs_of(s, custom_post_id=out2.id)
        chk(len(pj2) == 1 and pj2[0].status == JobStatus.PENDING,
            "задание сразу в работе", str([j.status.value for j in pj2]))

        print("\n[11] Заголовок предпросмотра называет вид и срок")
        head_now = preview._head(preview.POST, 2, None)
        head_late = preview._head(preview.DISCOUNT, 1, when)
        chk("Пост" in head_now and "Каналов: 2" in head_now, "вид и число каналов", head_now)
        chk("сразу после подтверждения" in head_now, "немедленная публикация помечена")
        chk("Объявление о скидке" in head_late and "выйдет" in head_late,
            "отложенная публикация показывает время", head_late)
        for kind in (preview.ITEM, preview.POST, preview.DISCOUNT, preview.DROP, preview.BUMP):
            kb = preview.keyboard(uuid.uuid4(), kind)
            cbs = [b["callback_data"] for b in kb["inline_keyboard"][0]]
            chk(all(len(c.encode()) <= 64 for c in cbs), f"callback_data влезает в лимит ({kind})",
                str(cbs))
    finally:
        async with SessionLocal() as s:
            await s.execute(delete(PostJob).where(PostJob.store_id == sid))
            await s.execute(delete(Discount).where(Discount.store_id == sid))
            await s.execute(delete(CustomPost).where(CustomPost.store_id == sid))
            drops = (await s.execute(select(Drop.id).where(Drop.store_id == sid))).scalars().all()
            await s.execute(delete(DropItem).where(DropItem.drop_id.in_(drops)))
            await s.execute(delete(Drop).where(Drop.store_id == sid))
            await s.execute(delete(AuditLog).where(AuditLog.store_id == sid))
            chans = (await s.execute(select(Channel.id).where(Channel.store_id == sid))).scalars().all()
            await s.execute(delete(ItemPost).where(ItemPost.channel_id.in_(chans)))
            await s.execute(delete(Channel).where(Channel.store_id == sid))
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
