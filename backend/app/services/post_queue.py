"""Очередь публикаций в канал: постановка, захват, ретраи, лимиты.

Захват идёт через SELECT ... FOR UPDATE SKIP LOCKED — два воркера (или
два процесса uvicorn) никогда не возьмут одно задание. Неудача не теряется:
попытка откладывается с экспоненциальным откатом, пока не кончатся попытки.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import JobKind, JobStatus, PostJob

log = logging.getLogger("queue")

# Telegram пропускает примерно 20 сообщений в минуту в один канал.
# Держим паузу с запасом, чтобы не ловить 429 и не терять посты.
MIN_GAP_SECONDS = 3.5
BASE_BACKOFF_SECONDS = 15
MAX_BACKOFF_SECONDS = 15 * 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def enqueue(
    session: AsyncSession,
    *,
    store_id: uuid.UUID,
    kind: JobKind,
    channel_id: str,
    channel_uid: uuid.UUID | None = None,
    item_id: uuid.UUID | None = None,
    message_id: int | None = None,
    delay_seconds: int = 0,
) -> PostJob:
    """Ставит задание. Коммитит вызывающий."""
    job = PostJob(
        store_id=store_id,
        item_id=item_id,
        kind=kind,
        channel_id=channel_id,
        channel_uid=channel_uid,
        message_id=message_id,
        run_after=_now() + timedelta(seconds=delay_seconds),
    )
    session.add(job)
    return job


async def has_pending(
    session: AsyncSession, item_id: uuid.UUID, kind: JobKind
) -> bool:
    """Есть ли уже незавершённое такое же задание — защита от дублей."""
    row = (
        await session.execute(
            select(PostJob.id).where(
                PostJob.item_id == item_id,
                PostJob.kind == kind,
                PostJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
            ).limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def claim(session: AsyncSession, limit: int = 5) -> list[PostJob]:
    """Забирает готовые задания и помечает их RUNNING.

    SKIP LOCKED пропускает строки, которые уже держит другой воркер, —
    поэтому параллельные заборы не конфликтуют и не ждут друг друга.
    """
    rows = (
        await session.execute(
            select(PostJob)
            .where(PostJob.status == JobStatus.PENDING, PostJob.run_after <= _now())
            .order_by(PostJob.run_after)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    for job in rows:
        job.status = JobStatus.RUNNING
    await session.commit()
    return list(rows)


async def mark_done(session: AsyncSession, job_id: uuid.UUID) -> None:
    await session.execute(
        update(PostJob)
        .where(PostJob.id == job_id)
        .values(status=JobStatus.DONE, last_error=None)
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def mark_failed(session: AsyncSession, job: PostJob, error: str) -> None:
    """Откладывает повтор или окончательно хоронит задание."""
    attempts = job.attempts + 1
    if attempts >= job.max_attempts:
        status, run_after = JobStatus.FAILED, job.run_after
        log.warning("job %s окончательно провален: %s", job.id, error)
    else:
        status = JobStatus.PENDING
        backoff = min(BASE_BACKOFF_SECONDS * (2 ** (attempts - 1)), MAX_BACKOFF_SECONDS)
        run_after = _now() + timedelta(seconds=backoff)
    await session.execute(
        update(PostJob)
        .where(PostJob.id == job.id)
        .values(
            status=status,
            attempts=attempts,
            run_after=run_after,
            last_error=error[:2000],
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def release_stuck(session: AsyncSession, older_than_minutes: int = 10) -> int:
    """Возвращает в очередь задания, зависшие в RUNNING после падения воркера."""
    cutoff = _now() - timedelta(minutes=older_than_minutes)
    res = await session.execute(
        update(PostJob)
        .where(PostJob.status == JobStatus.RUNNING, PostJob.updated_at < cutoff)
        .values(status=JobStatus.PENDING)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return res.rowcount or 0
