"""Свободные посты и расписание публикаций (контент-календарь)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import OWNER_ONLY, get_active_membership, get_current_user, require_role
from ..db import get_session
from ..models import (
    Channel,
    CustomPost,
    CustomPostStatus,
    JobKind,
    JobStatus,
    PostJob,
    StoreMember,
    User,
)
from ..schemas import CustomPostCreate, CustomPostOut
from ..services import audit, post_queue, preview, preview_hold

router = APIRouter(prefix="/api/v1/posts", tags=["posts"])

MAX_AHEAD_DAYS = 365


def _out(p: CustomPost) -> CustomPostOut:
    return CustomPostOut(
        id=p.id,
        body=p.body,
        photo_count=len(p.photo_file_ids or []),
        scheduled_at=p.scheduled_at,
        status=p.status.value,
        created_at=p.created_at,
    )


@router.get("", response_model=list[CustomPostOut])
async def list_posts(
    include_done: bool = Query(False, description="показать опубликованные и отменённые"),
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    q = select(CustomPost).where(CustomPost.store_id == member.store_id)
    if not include_done:
        q = q.where(CustomPost.status == CustomPostStatus.SCHEDULED)
    rows = (
        await session.execute(q.order_by(CustomPost.scheduled_at.nulls_first()))
    ).scalars().all()
    return [_out(p) for p in rows]


@router.post("", response_model=CustomPostOut, status_code=201)
async def create_post(
    payload: CustomPostCreate,
    user: User = Depends(get_current_user),
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    """Ставит пост в расписание. Без даты — публикуется сразу."""
    when = payload.scheduled_at
    if when is not None:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        # Небольшой допуск: пока форма заполнялась, время могло уйти в прошлое.
        if (now - when).total_seconds() > 300:
            raise HTTPException(422, "Время публикации в прошлом")
        if (when - now).days > MAX_AHEAD_DAYS:
            raise HTTPException(422, "Слишком далеко в будущем")

    channels = (
        await session.execute(
            select(Channel).where(
                Channel.store_id == member.store_id, Channel.enabled.is_(True)
            )
        )
    ).scalars().all()
    if not channels:
        raise HTTPException(422, "Нет включённых каналов — публиковать некуда")

    post = CustomPost(
        store_id=member.store_id,
        body=payload.body,
        photo_file_ids=list(payload.photo_file_ids or []),
        scheduled_at=when,
        created_by=user.id,
    )
    session.add(post)
    await session.flush()

    jobs = []
    for ch in channels:
        jobs.append(
            await post_queue.enqueue(
                session,
                store_id=member.store_id,
                kind=JobKind.CUSTOM_POST,
                channel_id=ch.chat_id,
                channel_uid=ch.id,
                custom_post_id=post.id,
                run_at=when,
            )
        )

    # Предпросмотр запрашиваем сразу при создании, даже если публикация
    # назначена на вечер: подтверждать пост в момент, когда он уже уходит,
    # владелец бы не успевал.
    hold = await preview.is_enabled(session, member.store_id)
    if hold:
        preview_hold.hold(jobs)
    audit.record(
        session,
        store_id=member.store_id,
        user_id=user.id,
        action=audit.POST_CREATE,
        summary=(payload.body or "")[:80] + ("" if when is None else " (по расписанию)"),
        entity_type="post",
        entity_id=post.id,
    )
    await session.commit()
    await session.refresh(post)

    if hold:
        photos = list(post.photo_file_ids or [])
        preview_hold.send_in_background(
            telegram_id=user.telegram_id,
            kind=preview.POST,
            entity_id=post.id,
            body=post.body,
            photo_entry=photos[0] if photos else None,
            channels=len(jobs),
            job_ids=[j.id for j in jobs],
            when=when,
        )
    return _out(post)


@router.delete("/{post_id}", status_code=204)
async def cancel_post(
    post_id: uuid.UUID,
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    """Отменяет запланированный пост и снимает его задания из очереди."""
    post = (
        await session.execute(
            select(CustomPost).where(
                CustomPost.id == post_id, CustomPost.store_id == member.store_id
            )
        )
    ).scalar_one_or_none()
    if post is None:
        raise HTTPException(404, "Post not found")
    if post.status == CustomPostStatus.PUBLISHED:
        raise HTTPException(409, "Пост уже опубликован — отменить нельзя")

    post.status = CustomPostStatus.CANCELLED
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.POST_CANCEL,
        summary=(post.body or "")[:80],
        entity_type="post",
        entity_id=post.id,
    )
    await session.execute(
        update(PostJob)
        .where(
            PostJob.custom_post_id == post.id,
            PostJob.status.in_((JobStatus.PENDING, JobStatus.AWAITING)),
        )
        .values(status=JobStatus.CANCELLED)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
