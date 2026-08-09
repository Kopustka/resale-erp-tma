"""Фоновый исполнитель очереди публикаций.

Живёт внутри процесса API как asyncio-задача. Захват заданий безопасен
для нескольких процессов (SKIP LOCKED), поэтому масштабирование uvicorn
дублей не создаст.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from ..db import SessionLocal
from ..models import (
    Channel,
    Item,
    ItemPost,
    ItemStatus,
    JobKind,
    JobStatus,
    PostJob,
    Store,
)
from . import post_queue, telegram_post

log = logging.getLogger("worker")

POLL_INTERVAL = 3.0        # пусто в очереди — спим столько
BATCH = 5
STUCK_SWEEP_EVERY = 60.0   # как часто искать зависшие RUNNING
BUMP_SCAN_EVERY = 900.0    # раз в 15 минут ищем вещи, которым пора наверх
BUMP_COOLDOWN_DAYS = 7     # не поднимаем одну вещь чаще этого

# Момент последней отправки в каждый канал: держим паузу между постами,
# чтобы не упереться в лимит Telegram.
_last_send: dict[str, float] = {}


async def _respect_rate_limit(channel_id: str) -> None:
    last = _last_send.get(channel_id)
    if last is not None:
        wait = post_queue.MIN_GAP_SECONDS - (time.monotonic() - last)
        if wait > 0:
            await asyncio.sleep(wait)
    _last_send[channel_id] = time.monotonic()


async def _load_context(session, job: PostJob):
    """Свежие данные вещи и склада на момент исполнения, а не постановки."""
    from ..routers.items import _default_template_body, _item_to_post_dict, _watermark_text

    item = (
        await session.execute(select(Item).where(Item.id == job.item_id))
    ).scalar_one_or_none()
    if item is None:
        return None
    store = (
        await session.execute(select(Store).where(Store.id == job.store_id))
    ).scalar_one_or_none()
    # Подпись берём канальную, если задана: у разных каналов она может отличаться.
    signature = store.channel_signature if store else None
    if job.channel_uid is not None:
        ch = (
            await session.execute(select(Channel).where(Channel.id == job.channel_uid))
        ).scalar_one_or_none()
        if ch is not None and ch.signature:
            signature = ch.signature
    return {
        "post": _item_to_post_dict(item),
        "signature": signature,
        "template": await _default_template_body(session, job.store_id),
        "watermark": await _watermark_text(session, job.store_id),
    }


async def _run_job(job: PostJob) -> None:
    async with SessionLocal() as session:
        try:
            ctx = await _load_context(session, job)
            if ctx is None:
                await post_queue.mark_done(session, job.id)  # вещь удалили — не ошибка
                return

            if job.kind == JobKind.POST_ITEM:
                already = (
                    await session.execute(
                        select(ItemPost.id).where(
                            ItemPost.item_id == job.item_id,
                            ItemPost.channel_id == job.channel_uid,
                        )
                    )
                ).scalar_one_or_none()
                if already is not None:
                    await post_queue.mark_done(session, job.id)  # уже опубликовано
                    return
                await _respect_rate_limit(job.channel_id)
                msg_id = await telegram_post.post_item(
                    job.channel_id,
                    ctx["post"],
                    ctx["signature"],
                    ctx["template"],
                    ctx["watermark"],
                )
                if msg_id is not None:
                    if job.channel_uid is not None:
                        session.add(
                            ItemPost(
                                item_id=job.item_id,
                                channel_id=job.channel_uid,
                                message_id=msg_id,
                            )
                        )
                    # legacy-поле: первый пост, чтобы старые места не сломались
                    await session.execute(
                        update(Item)
                        .where(Item.id == job.item_id, Item.channel_message_id.is_(None))
                        .values(channel_message_id=msg_id)
                        .execution_options(synchronize_session=False)
                    )
                    await session.commit()

            elif job.kind == JobKind.EDIT_CAPTION:
                if job.message_id is None:
                    await post_queue.mark_done(session, job.id)
                    return
                await _respect_rate_limit(job.channel_id)
                await telegram_post.edit_caption(
                    job.channel_id,
                    job.message_id,
                    ctx["post"],
                    ctx["signature"],
                    ctx["template"],
                    prefix=job.caption_prefix or "",
                )

            elif job.kind == JobKind.BUMP:
                # Поднятие = удалить старый пост и опубликовать заново, чтобы
                # вещь оказалась наверху ленты канала.
                post = (
                    await session.execute(
                        select(ItemPost).where(
                            ItemPost.item_id == job.item_id,
                            ItemPost.channel_id == job.channel_uid,
                        )
                    )
                ).scalar_one_or_none()
                if post is None or post.sold_marked:
                    await post_queue.mark_done(session, job.id)
                    return
                await _respect_rate_limit(job.channel_id)
                await telegram_post.delete_message(job.channel_id, post.message_id)
                new_id = await telegram_post.post_item(
                    job.channel_id,
                    ctx["post"],
                    ctx["signature"],
                    ctx["template"],
                    ctx["watermark"],
                )
                if new_id is not None:
                    post.message_id = new_id
                await session.execute(
                    update(Item)
                    .where(Item.id == job.item_id)
                    .values(bumped_at=datetime.now(timezone.utc))
                    .execution_options(synchronize_session=False)
                )
                await session.commit()

            elif job.kind == JobKind.MARK_SOLD:
                message_id = job.message_id
                if message_id is None:
                    await post_queue.mark_done(session, job.id)  # нечего править
                    return
                await _respect_rate_limit(job.channel_id)
                await telegram_post.mark_sold(
                    job.channel_id,
                    message_id,
                    ctx["post"],
                    ctx["signature"],
                    ctx["template"],
                )
                await session.execute(
                    update(ItemPost)
                    .where(
                        ItemPost.item_id == job.item_id,
                        ItemPost.channel_id == job.channel_uid,
                    )
                    .values(sold_marked=True)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()

            await post_queue.mark_done(session, job.id)

        except Exception as e:  # noqa: BLE001
            await session.rollback()
            await post_queue.mark_failed(session, job, f"{type(e).__name__}: {e}")


async def scan_for_bumps(session) -> int:
    """Ставит поднятие вещам, которые давно висят и давно не поднимались.

    Возвращает число поставленных заданий. Вынесено отдельно от цикла,
    чтобы правило отбора можно было проверить тестом без воркера.
    """
    now = datetime.now(timezone.utc)
    stores = (
        await session.execute(
            select(Store).where(Store.bump_enabled.is_(True))
        )
    ).scalars().all()
    queued = 0
    for store in stores:
        listed_before = now - timedelta(days=store.bump_after_days)
        cooldown = now - timedelta(days=BUMP_COOLDOWN_DAYS)
        rows = (
            await session.execute(
                select(Item, ItemPost, Channel)
                .join(ItemPost, ItemPost.item_id == Item.id)
                .join(Channel, Channel.id == ItemPost.channel_id)
                .where(
                    Item.store_id == store.id,
                    Item.status == ItemStatus.LISTED,
                    Item.archived_at.is_(None),
                    Item.listed_date <= listed_before,
                    ItemPost.sold_marked.is_(False),
                    Channel.enabled.is_(True),
                    (Item.bumped_at.is_(None)) | (Item.bumped_at <= cooldown),
                )
            )
        ).all()
        for item, post, ch in rows:
            busy = (
                await session.execute(
                    select(PostJob.id).where(
                        PostJob.item_id == item.id,
                        PostJob.kind == JobKind.BUMP,
                        PostJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                    ).limit(1)
                )
            ).scalar_one_or_none()
            if busy is not None:
                continue
            await post_queue.enqueue(
                session,
                store_id=store.id,
                kind=JobKind.BUMP,
                channel_id=ch.chat_id,
                channel_uid=ch.id,
                item_id=item.id,
                message_id=post.message_id,
            )
            queued += 1
    if queued:
        await session.commit()
    return queued


async def run_forever() -> None:
    log.info("очередь публикаций запущена")
    last_sweep = 0.0
    last_bump_scan = time.monotonic()  # первый скан — не сразу после старта
    while True:
        try:
            now = time.monotonic()
            if now - last_bump_scan > BUMP_SCAN_EVERY:
                last_bump_scan = now
                async with SessionLocal() as s:
                    n = await scan_for_bumps(s)
                if n:
                    log.info("поставлено поднятий: %s", n)

            if now - last_sweep > STUCK_SWEEP_EVERY:
                last_sweep = now
                async with SessionLocal() as s:
                    freed = await post_queue.release_stuck(s)
                if freed:
                    log.warning("вернул в очередь зависших заданий: %s", freed)

            async with SessionLocal() as s:
                jobs = await post_queue.claim(s, BATCH)

            if not jobs:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            for job in jobs:
                await _run_job(job)

        except asyncio.CancelledError:
            log.info("очередь публикаций остановлена")
            raise
        except Exception as e:  # noqa: BLE001
            # Воркер не имеет права умереть: любая неожиданность — пауза и дальше.
            log.exception("сбой цикла очереди: %s", e)
            await asyncio.sleep(POLL_INTERVAL)
