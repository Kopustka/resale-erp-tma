"""Админ-панель владельца склада: лента действий и сводка по команде.

Каждый эндпоинт закрыт `require_role(OWNER_ONLY)`, то есть доступен только
участнику с ролью OWNER на СВОЁМ активном складе. Сотрудник и аналитик
получают 403 — и панель им не отдаётся даже прямым запросом, мимо интерфейса.

Привязки к конкретному юзернейму здесь нет намеренно: юзернейм в Telegram
меняется в один тап, и «панель для @konstantinveliki» после переименования
либо потеряла бы владельца, либо досталась бы тому, кто занял освободившийся
ник. Роль OWNER хранится в store_members и такой подмене не подвержена.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import OWNER_ONLY, require_role
from ..db import get_session
from ..models import (
    AuditLog,
    Discount,
    Item,
    ItemStatus,
    ItemStatusLog,
    InviteStatus,
    Role,
    StoreInvite,
    StoreMember,
    User,
)
from ..schemas import (
    ActivityActor,
    ActivityEvent,
    ActivityPage,
    MemberStats,
    PendingInvite,
    TeamOverview,
)
from ..services import audit

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

MAX_DAYS = 365

STATUS_LABELS = {
    ItemStatus.BOUGHT: "Куплен",
    ItemStatus.PREPARING: "Подготовка",
    ItemStatus.PHOTOGRAPHED: "Отфотографирован",
    ItemStatus.LISTED: "Выставлен",
    ItemStatus.SHIPPED: "Отправлен",
}


def _status_name(s: ItemStatus | None) -> str:
    if s is None:
        return "—"
    return STATUS_LABELS.get(s, s.value)


async def _actors(
    session: AsyncSession, store_id: uuid.UUID, ids: set[uuid.UUID]
) -> dict[uuid.UUID, ActivityActor]:
    """Имена авторов событий по их id.

    Членство подтягиваем LEFT JOIN'ом: автор давнего действия мог уже быть
    исключён из склада, но его записи в журнале остаются — при INNER JOIN
    лента показала бы «кто-то» там, где человек известен по имени.
    """
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(User, StoreMember.role)
            .outerjoin(
                StoreMember,
                (StoreMember.user_id == User.id) & (StoreMember.store_id == store_id),
            )
            .where(User.id.in_(ids))
        )
    ).all()
    return {
        u.id: ActivityActor(
            user_id=u.id, username=u.username, first_name=u.first_name, role=role
        )
        for u, role in rows
    }


@router.get("/activity", response_model=ActivityPage)
async def activity(
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
    user_id: uuid.UUID | None = Query(None, description="только действия одного участника"),
    group: str | None = Query(None, description="items | status | publishing | settings"),
    days: int = Query(30, ge=1, le=MAX_DAYS),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Лента действий склада: журнал + история смен статуса, новое сверху.

    Две таблицы сливаются в памяти, а не UNION'ом в SQL: у них разный набор
    колонок, и запрос-склейка читался бы хуже, чем этот merge, при том же
    результате. Чтобы срез был честным, из каждой таблицы берём offset+limit
    строк — тогда после сортировки нужное окно точно окажется внутри.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    take = offset + limit + 1  # +1 — узнать, есть ли ещё страница
    events: list[ActivityEvent] = []

    want_status = group in (None, "status")
    want_audit = group != "status"

    if want_audit:
        q = (
            select(AuditLog)
            .where(AuditLog.store_id == member.store_id, AuditLog.created_at >= since)
            .order_by(AuditLog.created_at.desc())
            .limit(take)
        )
        if user_id is not None:
            q = q.where(AuditLog.user_id == user_id)
        for row in (await session.execute(q)).scalars():
            g = audit.group_of(row.action)
            if group is not None and g != group:
                continue
            icon, title = audit.label_of(row.action)
            events.append(
                ActivityEvent(
                    id=f"a:{row.id}",
                    at=row.created_at,
                    actor=ActivityActor(user_id=row.user_id),
                    action=row.action,
                    group=g,
                    icon=icon,
                    title=title,
                    summary=row.summary or "",
                    entity_type=row.entity_type,
                    entity_id=row.entity_id,
                )
            )

    if want_status:
        q = (
            select(ItemStatusLog, Item.sku, Item.title)
            .join(Item, Item.id == ItemStatusLog.item_id)
            .where(Item.store_id == member.store_id, ItemStatusLog.created_at >= since)
            .order_by(ItemStatusLog.created_at.desc())
            .limit(take)
        )
        if user_id is not None:
            q = q.where(ItemStatusLog.changed_by == user_id)
        for log, sku, title in (await session.execute(q)).all():
            what = f"{_status_name(log.old_status)} → {_status_name(log.new_status)}"
            events.append(
                ActivityEvent(
                    id=f"s:{log.id}",
                    at=log.created_at,
                    actor=ActivityActor(user_id=log.changed_by),
                    action="status",
                    group="status",
                    icon="🔄",
                    title="Сменил статус",
                    summary=f"{sku or ''} {title or ''}".strip() + f" · {what}",
                    entity_type="item",
                    entity_id=log.item_id,
                )
            )

    events.sort(key=lambda e: e.at, reverse=True)
    window = events[offset : offset + limit]
    has_more = len(events) > offset + limit

    ids = {e.actor.user_id for e in window if e.actor.user_id is not None}
    known = await _actors(session, member.store_id, ids)
    for e in window:
        if e.actor.user_id is not None:
            e.actor = known.get(e.actor.user_id, e.actor)

    return ActivityPage(events=window, has_more=has_more)


@router.get("/team", response_model=TeamOverview)
async def team(
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
    days: int = Query(30, ge=1, le=MAX_DAYS),
):
    """Кто в команде и что каждый сделал за период."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    store_id = member.store_id

    rows = (
        await session.execute(
            select(User, StoreMember)
            .join(StoreMember, StoreMember.user_id == User.id)
            .where(StoreMember.store_id == store_id)
            .order_by(StoreMember.created_at)
        )
    ).all()

    stats: dict[uuid.UUID, MemberStats] = {
        u.id: MemberStats(
            user_id=u.id,
            username=u.username,
            first_name=u.first_name,
            role=m.role,
            joined_at=m.created_at,
        )
        for u, m in rows
    }

    # Действия из журнала.
    for uid, cnt, last in (
        await session.execute(
            select(
                AuditLog.user_id,
                func.count(AuditLog.id),
                func.max(AuditLog.created_at),
            )
            .where(AuditLog.store_id == store_id, AuditLog.created_at >= since)
            .group_by(AuditLog.user_id)
        )
    ).all():
        st = stats.get(uid)
        if st is None:
            continue
        st.operations += cnt
        st.last_action_at = max(filter(None, (st.last_action_at, last)), default=None)

    # Смены статуса: и в общий счётчик операций, и по отдельным статусам.
    for uid, new_status, cnt, last in (
        await session.execute(
            select(
                ItemStatusLog.changed_by,
                ItemStatusLog.new_status,
                func.count(ItemStatusLog.id),
                func.max(ItemStatusLog.created_at),
            )
            .join(Item, Item.id == ItemStatusLog.item_id)
            .where(Item.store_id == store_id, ItemStatusLog.created_at >= since)
            .group_by(ItemStatusLog.changed_by, ItemStatusLog.new_status)
        )
    ).all():
        st = stats.get(uid)
        if st is None:
            continue
        st.operations += cnt
        if new_status == ItemStatus.LISTED:
            st.listed += cnt
        elif new_status == ItemStatus.SHIPPED:
            st.shipped += cnt
        st.last_action_at = max(filter(None, (st.last_action_at, last)), default=None)

    # Добавленные вещи считаем по самой вещи, а не по журналу: журнал ведётся
    # с этой версии, а purchaser_id проставлялся с самого начала — иначе у
    # склада с историей панель показала бы ноль добавлений.
    for uid, cnt in (
        await session.execute(
            select(Item.purchaser_id, func.count(Item.id))
            .where(Item.store_id == store_id, Item.created_at >= since)
            .group_by(Item.purchaser_id)
        )
    ).all():
        st = stats.get(uid)
        if st is not None:
            st.items_added = cnt

    for uid, cnt in (
        await session.execute(
            select(Discount.created_by, func.count(Discount.id))
            .where(Discount.store_id == store_id, Discount.created_at >= since)
            .group_by(Discount.created_by)
        )
    ).all():
        st = stats.get(uid)
        if st is not None:
            st.discounts = cnt

    invites = (
        await session.execute(
            select(StoreInvite)
            .where(
                StoreInvite.store_id == store_id,
                StoreInvite.status == InviteStatus.PENDING,
            )
            .order_by(StoreInvite.created_at.desc())
        )
    ).scalars().all()

    ordered = sorted(
        stats.values(),
        key=lambda s: (s.role != Role.OWNER, -s.operations, s.username or ""),
    )
    return TeamOverview(
        days=days,
        members=ordered,
        invites=[PendingInvite.model_validate(i) for i in invites],
    )
