"""Постановка публикации «на подтверждение».

Вынесено из роутеров, потому что шаги одни и те же для поста, скидки и
дропа: перевести задания в AWAITING, отправить владельцу предпросмотр,
а если доставить не удалось — снять блокировку.

Последнее важно: без этого отказ Telegram (бот заблокирован, диалог не
начат) навсегда подвесил бы публикацию в AWAITING, и владелец не увидел
бы ни поста, ни причины.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

from sqlalchemy import select, update

from ..db import SessionLocal
from ..models import JobStatus, PostJob
from . import preview

log = logging.getLogger("preview")


def hold(jobs: list[PostJob]) -> None:
    """Пометить задания ожидающими подтверждения. Коммитит вызывающий."""
    for job in jobs:
        job.status = JobStatus.AWAITING


async def release(job_ids: list[uuid.UUID]) -> None:
    """Снять ожидание и пустить задания в работу."""
    if not job_ids:
        return
    async with SessionLocal() as session:
        await session.execute(
            update(PostJob)
            .where(PostJob.id.in_(job_ids), PostJob.status == JobStatus.AWAITING)
            .values(status=JobStatus.PENDING)
            .execution_options(synchronize_session=False)
        )
        await session.commit()


def send_in_background(
    *,
    telegram_id: int,
    kind: str,
    entity_id: uuid.UUID,
    body: str,
    photo_entry: str | None,
    channels: int,
    job_ids: list[uuid.UUID],
    watermark: str | None = None,
    when: datetime | None = None,
) -> None:
    """Отправить предпросмотр вне HTTP-запроса.

    Внутри запроса отправка грузит фото в Telegram и держит интерфейс
    несколько секунд — пользователь видит это как зависший экран.
    """

    async def _task() -> None:
        try:
            sent = await preview.send_entity_preview(
                telegram_id,
                kind,
                entity_id,
                body,
                photo_entry,
                channels,
                watermark,
                when,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("предпросмотр %s %s не ушёл: %s", kind, entity_id, e)
            sent = False
        if not sent:
            log.warning(
                "предпросмотр %s %s не доставлен — публикуем без подтверждения",
                kind,
                entity_id,
            )
            await release(job_ids)

    asyncio.create_task(_task())


async def awaiting_job_ids(session, **filters) -> list[uuid.UUID]:
    """id заданий сущности, ждущих подтверждения."""
    q = select(PostJob.id).where(PostJob.status == JobStatus.AWAITING)
    for field, value in filters.items():
        q = q.where(getattr(PostJob, field) == value)
    return list((await session.execute(q)).scalars())
