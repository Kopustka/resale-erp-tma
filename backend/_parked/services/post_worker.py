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


async def _run_custom(session, job: PostJob) -> None:
    """Свободный пост: текст и, если есть, фото альбомом."""
    from ..models import CustomPost, CustomPostStatus
    from ..routers.items import _watermark_text
    from . import drops

    post = (
        await session.execute(
            select(CustomPost).where(CustomPost.id == job.custom_post_id)
        )
    ).scalar_one_or_none()
    if post is None or post.status == CustomPostStatus.CANCELLED:
        await post_queue.mark_done(session, job.id)  # отменили, пока ждал
        return

    await _respect_rate_limit(job.channel_id)
    photos = list(post.photo_file_ids or [])
    if photos:
        wm = await _watermark_text(session, job.store_id)
        entries = drops.entries_from_photos(photos, wm)
        if entries:
            await telegram_post.post_album(job.channel_id, entries, post.body)
        else:
            await telegram_post.send_text(job.channel_id, post.body)
    else:
        await telegram_post.send_text(job.channel_id, post.body)

    await session.execute(
        update(CustomPost)
        .where(CustomPost.id == post.id)
        .values(status=CustomPostStatus.PUBLISHED)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    await post_queue.mark_done(session, job.id)


async def _run_drop(session, job: PostJob) -> None:
    """Публикация подборки альбомом: одно сообщение на фото, подпись на первом."""
    from ..routers.items import _watermark_text
    from . import drops

    drop, items = await drops.load(session, job.drop_id)
    if drop is None or not items:
        await post_queue.mark_done(session, job.id)
        return

    store = (
        await session.execute(select(Store).where(Store.id == job.store_id))
    ).scalar_one_or_none()
    signature = store.channel_signature if store else None
    if job.channel_uid is not None:
        ch = (
            await session.execute(select(Channel).where(Channel.id == job.channel_uid))
        ).scalar_one_or_none()
        if ch is not None and ch.signature:
            signature = ch.signature

    wm = await _watermark_text(session, job.store_id)
    entries = drops.photo_entries(items, wm)
    caption = drops.build_caption(drop, items, signature)

    await _respect_rate_limit(job.channel_id)
    ids = await telegram_post.post_album(job.channel_id, entries, caption)

    # Раскладываем message_id по вещам: позиция в альбоме = позиция в списке.
    # Вещи без фото в альбом не попали, поэтому идём по тем, у кого фото есть.
    with_photo = [it for it in items if (it.photo_file_ids or [])][: len(ids)]
    for it, mid in zip(with_photo, ids):
        exists = (
            await session.execute(
                select(ItemPost.id).where(
                    ItemPost.item_id == it.id, ItemPost.channel_id == job.channel_uid
                )
            )
        ).scalar_one_or_none()
        if exists is None and job.channel_uid is not None:
            session.add(
                ItemPost(item_id=it.id, channel_id=job.channel_uid, message_id=mid)
            )
    await session.commit()
    await post_queue.mark_done(session, job.id)


async def _run_job(job: PostJob) -> None:
    async with SessionLocal() as session:
        try:
            if job.kind == JobKind.CUSTOM_POST:
                await _run_custom(session, job)
                return

            if job.kind == JobKind.DROP_POST:
                await _run_drop(session, job)
                return

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

            elif job.kind == JobKind.UNPUBLISH:
                # Вещь вернули до «выставлен» — пост в канале висеть не должен,
                # иначе состояние расходится и повторная публикация молча
                # пропускается защитой от дублей.
                if job.message_id is not None:
                    await _respect_rate_limit(job.channel_id)
                    await telegram_post.delete_message(job.channel_id, job.message_id)
                await session.execute(
                    ItemPost.__table__.delete().where(
                        ItemPost.item_id == job.item_id,
                        ItemPost.channel_id == job.channel_uid,
                    )
                )
                # legacy-поле держим в согласии с item_posts
                left = (
                    await session.execute(
                        select(ItemPost.message_id).where(ItemPost.item_id == job.item_id)
                    )
                ).scalars().first()
                await session.execute(
                    update(Item)
                    .where(Item.id == job.item_id)
                    .values(channel_message_id=left)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()

            elif job.kind == JobKind.DISCOUNT_POST:
                from ..models import Discount, DiscountStatus
                from .discounts import build_message
                from .fx import symbol as cur_symbol

                d = (
                    await session.execute(
                        select(Discount).where(Discount.id == job.discount_id)
                    )
                ).scalar_one_or_none()
                if d is None or d.status == DiscountStatus.CANCELLED:
                    await post_queue.mark_done(session, job.id)  # отменили, пока ждал
                    return
                item = (
                    await session.execute(select(Item).where(Item.id == job.item_id))
                ).scalar_one_or_none()
                # Отложенную скидку применяем к вещи в момент публикации:
                # до этого она продавалась по старой цене, и менять её
                # заранее было бы неверно. Повторный прогон по второму
                # каналу ничего не испортит — значения те же.
                if item is not None and item.list_price_orig != d.new_price:
                    from .fx import convert as fx_convert

                    item.price_before_discount = d.old_price
                    item.list_price_orig = d.new_price
                    item.list_price = await fx_convert(
                        d.new_price, d.currency, item.cost_currency or "BYN"
                    )
                    await session.commit()
                await _respect_rate_limit(job.channel_id)
                store = (
                    await session.execute(
                        select(Store).where(Store.id == job.store_id)
                    )
                ).scalar_one_or_none()
                signature = store.channel_signature if store else None
                if job.channel_uid is not None:
                    ch = (
                        await session.execute(
                            select(Channel).where(Channel.id == job.channel_uid)
                        )
                    ).scalar_one_or_none()
                    if ch is not None and ch.signature:
                        signature = ch.signature
                await telegram_post.reply_to(
                    job.channel_id,
                    job.message_id,
                    build_message(
                        d,
                        item,
                        cur_symbol(d.currency),
                        store.discount_template if store else None,
                        signature,
                    ),
                )
                await session.execute(
                    update(Discount)
                    .where(Discount.id == d.id)
                    .values(status=DiscountStatus.PUBLISHED)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()

            elif job.kind == JobKind.NOTIFY_SUB:
                from .preview import notify_subscriber

                await _respect_rate_limit(job.channel_id)
                delivered = await notify_subscriber(
                    int(job.channel_id),
                    job.sub_id,
                    ctx["post"],
                    ctx["signature"],
                    ctx["template"],
                )
                if not delivered:
                    # Бот заблокирован или чат недоступен — подписка мертва,
                    # гасим её, чтобы не долбиться в неё при каждой новинке.
                    from ..models import Subscription

                    await session.execute(
                        update(Subscription)
                        .where(Subscription.id == job.sub_id)
                        .values(active=False)
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
    # Предпросмотры шлём после коммита: внутри цикла отправка держала бы
    # транзакцию на время запросов к Telegram.
    pending_previews: list = []
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
            job = await post_queue.enqueue(
                session,
                store_id=store.id,
                kind=JobKind.BUMP,
                channel_id=ch.chat_id,
                channel_uid=ch.id,
                item_id=item.id,
                message_id=post.message_id,
            )
            # Поднятие тоже создаёт новое сообщение в канале, поэтому при
            # включённом предпросмотре спрашиваем владельца. Момента
            # «создания» у него нет — обход находит вещь сам, — так что
            # предпросмотр уходит в момент постановки задания.
            if store.preview_before_post:
                job.status = JobStatus.AWAITING
                pending_previews.append((store, item, ch, job))
            queued += 1
    if queued:
        await session.commit()
    for store, item, ch, job in pending_previews:
        await _send_bump_preview(store, item, job)
    return queued


async def _send_bump_preview(store: Store, item: Item, job: PostJob) -> None:
    """Спрашивает владельца перед поднятием вещи.

    Недоставленный предпросмотр не должен подвешивать очередь: если писать
    некому или бот заблокирован, поднимаем без подтверждения.
    """
    from ..models import User
    from . import preview

    async with SessionLocal() as s:
        owner = (
            await s.execute(select(User).where(User.id == store.owner_id))
        ).scalar_one_or_none()
        if owner is None:
            return
        ctx = await _load_context(s, job)
        if ctx is None:
            return
        caption = telegram_post.build_caption(
            ctx["post"], ctx["signature"], ctx["template"]
        )
        photos = ctx["post"].get("photo_file_ids") or []
        try:
            sent = await preview.send_entity_preview(
                owner.telegram_id,
                preview.BUMP,
                item.id,
                caption,
                photos[0] if photos else None,
                1,
                ctx["watermark"],
            )
        except Exception as e:  # noqa: BLE001
            log.warning("предпросмотр поднятия не ушёл: %s", e)
            sent = False
        if not sent:
            await s.execute(
                update(PostJob)
                .where(PostJob.id == job.id, PostJob.status == JobStatus.AWAITING)
                .values(status=JobStatus.PENDING)
                .execution_options(synchronize_session=False)
            )
            await s.commit()


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
