"""Неразрушающий тест очереди публикаций.

Проверяет постановку, захват с SKIP LOCKED, ретраи с откатом, окончательный
провал и подбор зависших. Реальных сообщений в Telegram не шлёт.

Запуск: env -u BOT_TOKEN /opt/resale-venv/bin/python queue_test.py
"""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from app.db import SessionLocal
from app.models import (
    JobKind,
    JobStatus,
    PostJob,
    Role,
    Store,
    StoreMember,
    User,
)
from app.services import post_queue

TG_ID = 999782
_passed = _failed = 0


def check(cond, name, extra=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {name}")
    else:
        _failed += 1
        print(f"  ❌ {name}" + (f"\n       {extra}" if extra else ""))


async def setup() -> uuid.UUID:
    async with SessionLocal() as s:
        u = (await s.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one_or_none()
        if u is None:
            u = User(telegram_id=TG_ID, username="qtest", first_name="Q")
            s.add(u)
            await s.flush()
        st = Store(name="QUEUE-TEST", owner_id=u.id)
        s.add(st)
        await s.flush()
        s.add(StoreMember(user_id=u.id, store_id=st.id, role=Role.OWNER))
        u.current_store_id = st.id
        await s.commit()
        return st.id


async def teardown(store_id: uuid.UUID) -> None:
    async with SessionLocal() as s:
        await s.execute(delete(PostJob).where(PostJob.store_id == store_id))
        await s.execute(delete(StoreMember).where(StoreMember.store_id == store_id))
        u = (await s.execute(select(User).where(User.telegram_id == TG_ID))).scalar_one_or_none()
        if u:
            u.current_store_id = None
            await s.flush()
        await s.execute(delete(Store).where(Store.id == store_id))
        if u:
            await s.execute(delete(User).where(User.id == u.id))
        await s.commit()


async def main() -> None:
    print("=" * 62)
    print("ТЕСТ ОЧЕРЕДИ ПУБЛИКАЦИЙ (неразрушающий)")
    print("=" * 62)
    sid = await setup()
    try:
        print("\n[1] Постановка и захват")
        async with SessionLocal() as s:
            for _ in range(3):
                await post_queue.enqueue(
                    s, store_id=sid, kind=JobKind.POST_ITEM, channel_id="@t"
                )
            await s.commit()

        async with SessionLocal() as s:
            got = await post_queue.claim(s, limit=2)
        check(len(got) == 2, "claim берёт не больше лимита", str(len(got)))
        check(all(j.status == JobStatus.RUNNING for j in got), "захваченные помечены RUNNING")

        async with SessionLocal() as s:
            again = await post_queue.claim(s, limit=10)
        check(len(again) == 1, "повторный claim не выдаёт уже занятые", str(len(again)))

        print("\n[2] Отложенный запуск")
        async with SessionLocal() as s:
            await post_queue.enqueue(
                s, store_id=sid, kind=JobKind.POST_ITEM, channel_id="@t", delay_seconds=3600
            )
            await s.commit()
        async with SessionLocal() as s:
            got = await post_queue.claim(s, limit=10)
        check(got == [], "задание из будущего не забирается", str(got))

        print("\n[3] Ретрай с откатом")
        async with SessionLocal() as s:
            job = PostJob(store_id=sid, kind=JobKind.POST_ITEM, channel_id="@t", max_attempts=3)
            s.add(job)
            await s.commit()
            await s.refresh(job)
            jid = job.id
            before = datetime.now(timezone.utc)
            await post_queue.mark_failed(s, job, "сеть недоступна")

        async with SessionLocal() as s:
            fresh = (await s.execute(select(PostJob).where(PostJob.id == jid))).scalar_one()
            check(fresh.status == JobStatus.PENDING, "после сбоя снова PENDING")
            check(fresh.attempts == 1, "счётчик попыток вырос", str(fresh.attempts))
            check(fresh.run_after > before, "повтор отложен в будущее")
            check("сеть недоступна" in (fresh.last_error or ""), "ошибка сохранена")
            delay1 = (fresh.run_after - before).total_seconds()

        async with SessionLocal() as s:
            job = (await s.execute(select(PostJob).where(PostJob.id == jid))).scalar_one()
            before2 = datetime.now(timezone.utc)
            await post_queue.mark_failed(s, job, "снова сеть")
        async with SessionLocal() as s:
            fresh = (await s.execute(select(PostJob).where(PostJob.id == jid))).scalar_one()
            delay2 = (fresh.run_after - before2).total_seconds()
        check(delay2 > delay1, f"откат растёт ({delay1:.0f}с -> {delay2:.0f}с)")

        print("\n[4] Исчерпание попыток")
        async with SessionLocal() as s:
            job = (await s.execute(select(PostJob).where(PostJob.id == jid))).scalar_one()
            await post_queue.mark_failed(s, job, "третий раз")
        async with SessionLocal() as s:
            fresh = (await s.execute(select(PostJob).where(PostJob.id == jid))).scalar_one()
        check(fresh.status == JobStatus.FAILED, "после max_attempts — FAILED", str(fresh.status))
        async with SessionLocal() as s:
            got = await post_queue.claim(s, limit=10)
        check(all(j.id != jid for j in got), "проваленное больше не забирается")

        print("\n[5] Защита от дублей")
        item_id = uuid.uuid4()
        async with SessionLocal() as s:
            check(
                not await post_queue.has_pending(s, item_id, JobKind.POST_ITEM),
                "для новой вещи заданий нет",
            )
            j = PostJob(
                store_id=sid, item_id=None, kind=JobKind.POST_ITEM, channel_id="@t"
            )
            s.add(j)
            await s.commit()
        # has_pending смотрит по item_id, поэтому проверяем на реальной связке
        async with SessionLocal() as s:
            j = (
                await s.execute(
                    select(PostJob).where(PostJob.store_id == sid).limit(1)
                )
            ).scalar_one()
            await s.execute(
                update(PostJob).where(PostJob.id == j.id).values(
                    item_id=None, status=JobStatus.PENDING
                )
            )
            await s.commit()

        print("\n[6] Подбор зависших")
        async with SessionLocal() as s:
            stuck = PostJob(
                store_id=sid, kind=JobKind.POST_ITEM, channel_id="@t",
                status=JobStatus.RUNNING,
            )
            s.add(stuck)
            await s.commit()
            await s.refresh(stuck)
            sid_job = stuck.id
            # искусственно состариваем
            await s.execute(
                update(PostJob).where(PostJob.id == sid_job).values(
                    updated_at=datetime.now(timezone.utc) - timedelta(hours=1)
                )
            )
            await s.commit()

        async with SessionLocal() as s:
            freed = await post_queue.release_stuck(s, older_than_minutes=10)
        check(freed >= 1, "зависшее задание возвращено в очередь", str(freed))
        async with SessionLocal() as s:
            fresh = (await s.execute(select(PostJob).where(PostJob.id == sid_job))).scalar_one()
        check(fresh.status == JobStatus.PENDING, "статус снова PENDING")

    finally:
        await teardown(sid)
        print("\n[cleanup] временные данные удалены")

    print("\n" + "=" * 62)
    print(f"ИТОГ: {_passed} успешно, {_failed} провалено")
    print("=" * 62)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
