"""Админ-панель: лента действий и сводка по команде.

Смотреть склад вправе двое: его владелец (роль OWNER в store_members) и
наблюдатель, которому владелец сам открыл доступ через store_oversight.
Сотрудник и аналитик не проходят никогда — и панель им не отдаётся даже
прямым запросом, мимо интерфейса.

Привязки к конкретному юзернейму здесь нет намеренно: юзернейм в Telegram
меняется в один тап, и «панель для @konstantinveliki» после переименования
либо потеряла бы владельца, либо досталась бы тому, кто занял освободившийся
ник. Роль OWNER хранится в store_members и такой подмене не подвержена.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import false as sa_false
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..db import get_session
from ..models import (
    AuditLog,
    Discount,
    Item,
    ItemStatus,
    ItemStatusLog,
    InviteStatus,
    OversightStatus,
    Role,
    Store,
    StoreInvite,
    StoreMember,
    StoreOversight,
    User,
)
from ..schemas import (
    ActivityActor,
    ActivityEvent,
    ActivityPage,
    MemberStats,
    OversightOut,
    OversightRequest,
    PendingInvite,
    ScopeOut,
    TeamOverview,
)
from ..services import audit
from ..services import oversight as ov

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

MAX_DAYS = 365

STATUS_LABELS = {
    ItemStatus.BOUGHT: "Куплен",
    ItemStatus.PREPARING: "Подготовка",
    ItemStatus.PHOTOGRAPHED: "Отфотографирован",
    ItemStatus.LISTED: "Выставлен",
    ItemStatus.SHIPPED: "Отправлен",
}



class Scope:
    """Разрешённая область просмотра: какой склад и на каком основании."""

    __slots__ = ("store_id", "as_owner")

    def __init__(self, store_id: uuid.UUID, as_owner: bool) -> None:
        self.store_id = store_id
        self.as_owner = as_owner


async def _owner_of(session: AsyncSession, user: User, store_id: uuid.UUID) -> bool:
    row = (
        await session.execute(
            select(StoreMember.id).where(
                StoreMember.user_id == user.id,
                StoreMember.store_id == store_id,
                StoreMember.role == Role.OWNER,
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def resolve_scope(
    store_id: uuid.UUID | None = Query(None, description="чужой склад под надзором"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Scope:
    """Единственная точка проверки прав на просмотр.

    Держим её одной зависимостью, а не повторяем условие в каждом
    обработчике: пропущенная проверка в одном месте открыла бы чужой склад
    целиком, а такую дыру легко не заметить при добавлении эндпоинта.
    """
    target = store_id or user.current_store_id
    if target is None:
        raise HTTPException(403, "Нет активного склада")
    if await _owner_of(session, user, target):
        return Scope(target, True)
    if await ov.can_watch(session, user.id, target):
        return Scope(target, False)
    raise HTTPException(403, "Нет доступа к админ-панели этого склада")


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
    scope: Scope = Depends(resolve_scope),
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
            .where(AuditLog.store_id == scope.store_id, AuditLog.created_at >= since)
            .order_by(AuditLog.created_at.desc())
            .limit(take)
        )
        if user_id is not None:
            q = q.where(AuditLog.user_id == user_id)
        if group is not None:
            # Отсев именно в SQL. Отбрасывать группы после LIMIT нельзя:
            # выборка берёт свежайшие записи независимо от группы, и вечер,
            # проведённый в настройках, выдавливал из ответа все события по
            # вещам — фильтр показывал пустоту вместо сотен записей.
            prefixes = audit.prefixes_of(group)
            if not prefixes:
                q = q.where(sa_false())
            else:
                cond = or_(*(AuditLog.action.startswith(f"{p}.") for p in prefixes))
                if group == "items":
                    # Неизвестный префикс тоже считается «вещами» — так же,
                    # как в group_of, иначе новая запись пропадёт из ленты.
                    cond = or_(
                        cond,
                        ~or_(
                            *(
                                AuditLog.action.startswith(f"{p}.")
                                for p in audit.ALL_PREFIXES
                            )
                        ),
                    )
                q = q.where(cond)
        for row in (await session.execute(q)).scalars():
            g = audit.group_of(row.action)
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
            .where(Item.store_id == scope.store_id, ItemStatusLog.created_at >= since)
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
    known = await _actors(session, scope.store_id, ids)
    for e in window:
        if e.actor.user_id is not None:
            e.actor = known.get(e.actor.user_id, e.actor)

    return ActivityPage(events=window, has_more=has_more)


@router.get("/team", response_model=TeamOverview)
async def team(
    scope: Scope = Depends(resolve_scope),
    session: AsyncSession = Depends(get_session),
    days: int = Query(30, ge=1, le=MAX_DAYS),
):
    """Кто в команде и что каждый сделал за период."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    store_id = scope.store_id

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


# --------------------------------------------------------------- надзор


@router.get("/scopes", response_model=list[ScopeOut])
async def scopes(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Склады, которые этот человек вправе открыть в админ-панели."""
    out: list[ScopeOut] = []

    own = (
        await session.execute(
            select(Store)
            .join(StoreMember, StoreMember.store_id == Store.id)
            .where(StoreMember.user_id == user.id, StoreMember.role == Role.OWNER)
            .order_by(Store.created_at)
        )
    ).scalars().all()
    out += [ScopeOut(store_id=s.id, name=s.name, kind="own") for s in own]

    watched = (
        await session.execute(
            select(Store, User)
            .join(StoreOversight, StoreOversight.store_id == Store.id)
            .outerjoin(User, User.id == Store.owner_id)
            .where(
                StoreOversight.watcher_id == user.id,
                StoreOversight.status == OversightStatus.ACTIVE,
            )
            .order_by(Store.name)
        )
    ).all()
    out += [
        ScopeOut(
            store_id=st.id,
            name=st.name,
            kind="watch",
            owner_name=(owner.first_name or owner.username) if owner else None,
        )
        for st, owner in watched
    ]
    return out


def _ov_out(r: StoreOversight, store_name: str | None = None) -> OversightOut:
    return OversightOut(
        id=r.id,
        target_username=r.target_username,
        store_id=r.store_id,
        store_name=store_name,
        status=r.status.value,
        created_at=r.created_at,
    )


@router.get("/oversight", response_model=list[OversightOut])
async def list_oversight(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Запросы, отправленные этим человеком: ожидающие и действующие."""
    rows = (
        await session.execute(
            select(StoreOversight, Store.name)
            .outerjoin(Store, Store.id == StoreOversight.store_id)
            .where(
                StoreOversight.watcher_id == user.id,
                StoreOversight.status.in_(
                    (OversightStatus.PENDING, OversightStatus.ACTIVE)
                ),
            )
            .order_by(StoreOversight.created_at.desc())
        )
    ).all()
    return [_ov_out(r, name) for r, name in rows]


@router.post("/oversight", response_model=OversightOut, status_code=201)
async def request_oversight(
    payload: OversightRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Попросить доступ к ленте чужого склада.

    Запись создаётся в PENDING и ждёт кнопки «Разрешить» в боте. Сразу
    подключать нельзя: сюда приходит любой юзернейм, и без согласия
    владельца это был бы способ читать чужой склад, зная только ник.
    Если человек ещё не открывал бота, запрос дождётся его первого /start.
    """
    uname = payload.username.lstrip("@").strip().lower()
    if not uname:
        raise HTTPException(422, "Пустой юзернейм")
    if user.username and uname == user.username.lower():
        raise HTTPException(422, "Это вы сами")

    dup = (
        await session.execute(
            select(StoreOversight).where(
                StoreOversight.watcher_id == user.id,
                StoreOversight.target_username == uname,
                StoreOversight.status.in_(
                    (OversightStatus.PENDING, OversightStatus.ACTIVE)
                ),
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(
            409,
            "Запрос уже отправлен"
            if dup.status == OversightStatus.PENDING
            else "Доступ уже открыт",
        )

    req = StoreOversight(watcher_id=user.id, target_username=uname)
    session.add(req)
    await session.flush()
    await session.commit()
    await session.refresh(req)

    # Спрашиваем после коммита: если Telegram не ответит, запрос всё равно
    # сохранён и уйдёт адресату при его следующем /start.
    await ov.deliver(session, req)
    return _ov_out(req, None)


@router.delete("/oversight/{req_id}", status_code=204)
async def drop_oversight(
    req_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Снять наблюдение. Вправе обе стороны: и наблюдатель, и владелец склада."""
    req = (
        await session.execute(select(StoreOversight).where(StoreOversight.id == req_id))
    ).scalar_one_or_none()
    if req is None:
        raise HTTPException(404, "Запрос не найден")

    is_watcher = req.watcher_id == user.id
    is_owner = req.store_id is not None and await _owner_of(session, user, req.store_id)
    if not (is_watcher or is_owner):
        raise HTTPException(403, "Нет прав на этот запрос")

    req.status = OversightStatus.REVOKED
    req.decided_at = datetime.now(timezone.utc)
    await session.commit()
