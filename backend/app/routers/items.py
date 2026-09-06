"""Роутер товаров: список (cursor), создание, смена статуса, архив, подсказки."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CAN_EDIT, get_active_membership, get_current_user
from ..db import get_session
from ..models import (
    Item,
    ItemStatus,
    Role,
    Store,
    StoreMember,
    User,
)
from ..repositories.items import ItemRepository
from ..config import get_settings
from ..schemas import (
    ItemCreate,
    ItemOut,
    ItemPage,
    ItemUpdate,
    StatusPatch,
)
from ..services import audit, fx, idempotency
from ..services import fields as fields_svc
from ..services.fsm import SOLD_STATUSES, can_transition, next_status

settings = get_settings()

router = APIRouter(prefix="/api/v1/items", tags=["items"])

FINANCE_FIELDS = (
    "cost_price", "restore_cost", "delivery_cost", "platform_fee",
    "selling_price", "net_profit", "roi_percent", "purchase_location",
)


def to_out(item: Item, *, show_finance: bool) -> ItemOut:
    data = {
        "id": item.id,
        "sku": item.sku,
        "title": item.title,
        "brand": item.brand,
        "category": item.category,
        "size": item.size,
        "color": item.color,
        "condition": item.condition,
        "length_cm": item.length_cm,
        "width_cm": item.width_cm,
        "sleeve_cm": item.sleeve_cm,
        "description": item.description,
        "photo_count": len(item.photo_file_ids or []),
        "status": item.status,
        "version": item.version,
        "listed_date": item.listed_date,
        "sold_date": item.sold_date,
        "created_at": item.created_at,
        "sales_platform": item.sales_platform,
        "extra": item.extra or {},
    }
    # list_price (цена в объявлении) видна всем ролям — это публичный ценник.
    data.update(
        list_price=item.list_price_orig,
        list_price_base=item.list_price,
        price_currency=item.price_currency,
        price_before_discount=item.price_before_discount,
    )
    if show_finance:
        data.update(
            cost_price=item.cost_price_orig,
            restore_cost=item.restore_cost_orig,
            delivery_cost=item.delivery_cost_orig,
            platform_fee=item.platform_fee_orig,
            selling_price=item.selling_price_orig,
            cost_currency=item.cost_currency,
            cost_price_base=item.cost_price,
            selling_price_base=item.selling_price,
            net_profit=item.net_profit,
            roi_percent=item.roi_percent,
            purchase_location=item.purchase_location,
        )
    return ItemOut(**data)


def _can_see_finance(member: StoreMember) -> bool:
    return member.role in (Role.OWNER, Role.ANALYST)


COST_FIELDS = ("cost_price", "restore_cost", "delivery_cost")
PRICE_FIELDS = ("platform_fee", "selling_price", "list_price")
MONEY_KEYS = COST_FIELDS + PRICE_FIELDS + ("cost_currency", "price_currency")


async def _store_base_currency(session: AsyncSession, store_id) -> str:
    cur = (
        await session.execute(select(Store.base_currency).where(Store.id == store_id))
    ).scalar_one_or_none()
    return (cur or "BYN").upper()


async def _build_money(money_in: dict, base: str) -> dict:
    """Из введённых сумм+валют собирает ORM-поля: orig (как ввели) + база (конверт)."""
    cost_cur = (money_in.get("cost_currency") or base).upper()
    price_cur = (money_in.get("price_currency") or base).upper()
    out: dict = {"cost_currency": cost_cur, "price_currency": price_cur}
    for f in COST_FIELDS:
        orig = money_in.get(f) or 0
        out[f"{f}_orig"] = orig
        out[f] = await fx.convert(orig, cost_cur, base)
    # platform_fee — обязательное число
    pf = money_in.get("platform_fee") or 0
    out["platform_fee_orig"] = pf
    out["platform_fee"] = await fx.convert(pf, price_cur, base)
    # selling_price / list_price — nullable
    for f in ("selling_price", "list_price"):
        orig = money_in.get(f)
        out[f"{f}_orig"] = orig
        out[f] = await fx.convert(orig, price_cur, base) if orig is not None else None
    return out


@router.get("", response_model=ItemPage)
async def list_items(
    cursor: str | None = None,
    limit: int = Query(30, le=100),
    status_filter: ItemStatus | None = Query(None, alias="status"),
    brand: str | None = None,
    search: str | None = None,
    ids: str | None = Query(None, description="CSV UUID для drill-down"),
    archived: bool = Query(False, description="показать архив вместо активных"),
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    repo = ItemRepository(session)
    id_list = None
    if ids:
        try:
            id_list = [uuid.UUID(x) for x in ids.split(",") if x]
        except ValueError:
            raise HTTPException(422, "Bad ids")
    rows, next_cursor = await repo.list_page(
        member.store_id,
        limit=limit,
        cursor=cursor,
        status=status_filter,
        brand=brand,
        search=search,
        ids=id_list,
        archived=archived,
    )
    show = _can_see_finance(member)
    return ItemPage(
        items=[to_out(r, show_finance=show) for r in rows],
        next_cursor=next_cursor,
    )


@router.get("/suggest/{field}")
async def suggest(
    field: str,
    q: str = "",
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    repo = ItemRepository(session)
    return await repo.suggest(member.store_id, field, q)


@router.get("/{item_id}", response_model=ItemOut)
async def get_item(
    item_id: uuid.UUID,
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    repo = ItemRepository(session)
    item = await repo.get(member.store_id, item_id, include_archived=True)
    if item is None:
        raise HTTPException(404, "Item not found")
    return to_out(item, show_finance=_can_see_finance(member))


@router.patch("/{item_id}", response_model=ItemOut)
async def edit_item(
    item_id: uuid.UUID,
    payload: ItemUpdate,
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    """Редактирование полей товара (в т.ч. состояние/condition, цены)."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot edit items")
    repo = ItemRepository(session)
    item = await repo.get(member.store_id, item_id, include_archived=True)
    if item is None:
        raise HTTPException(404, "Item not found")

    data = payload.model_dump(exclude_unset=True)

    # Свои поля дописываем поверх имеющихся, а не заменяем словарь целиком:
    # иначе правка одного поля стирала бы остальные.
    incoming_extra = data.pop("extra", None)
    if incoming_extra is not None:
        form = await fields_svc.ensure_defaults(session, member.store_id)
        merged = dict(item.extra or {})
        merged.update(fields_svc.filter_extra(incoming_extra, form))
        item.extra = merged

    # Деньги/валюты меняем только финансовым ролям и с полной переконвертацией.
    money_present = any(k in data for k in MONEY_KEYS)
    if money_present:
        if not _can_see_finance(member):
            for k in MONEY_KEYS:
                data.pop(k, None)
        else:
            base = await _store_base_currency(session, member.store_id)
            money_in = {
                "cost_currency": data.get("cost_currency", item.cost_currency),
                "price_currency": data.get("price_currency", item.price_currency),
                "cost_price": data.get("cost_price", item.cost_price_orig),
                "restore_cost": data.get("restore_cost", item.restore_cost_orig),
                "delivery_cost": data.get("delivery_cost", item.delivery_cost_orig),
                "platform_fee": data.get("platform_fee", item.platform_fee_orig),
                "selling_price": data.get("selling_price", item.selling_price_orig),
                "list_price": data.get("list_price", item.list_price_orig),
            }
            for k in MONEY_KEYS:
                data.pop(k, None)
            data.update(await _build_money(money_in, base))

    price_before = item.list_price_orig
    if data:
        await repo.update_fields(item, data)
        # Поля берём из исходного payload, а не из data: деньги там уже
        # раскрыты в пары *_orig/*_base, и владелец увидел бы в журнале
        # «cost_price_base», хотя сотрудник правил «закупку».
        touched = ", ".join(sorted(payload.model_dump(exclude_unset=True)))
        audit.record(
            session,
            store_id=member.store_id,
            user_id=member.user_id,
            action=audit.ITEM_EDIT,
            summary=f"{item.sku or ''} {item.title or ''}".strip() + f" · {touched}",
            entity_type="item",
            entity_id=item.id,
        )
        await session.commit()
    fresh = await repo.get(member.store_id, item_id, include_archived=True)
    return to_out(fresh, show_finance=_can_see_finance(member))


@router.post("/{item_id}/restore", response_model=ItemOut)
async def restore_item(
    item_id: uuid.UUID,
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    """Достать товар из архива."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot restore items")
    repo = ItemRepository(session)
    item = await repo.get(member.store_id, item_id, include_archived=True)
    if item is None:
        raise HTTPException(404, "Item not found")
    if item.archived_at is not None:
        await repo.restore(item)
        audit.record(
            session,
            store_id=member.store_id,
            user_id=member.user_id,
            action=audit.ITEM_RESTORE,
            summary=f"{item.sku or ''} {item.title or ''}".strip(),
            entity_type="item",
            entity_id=item.id,
        )
        await session.commit()
    fresh = await repo.get(member.store_id, item_id, include_archived=True)
    return to_out(fresh, show_finance=_can_see_finance(member))


@router.post("", response_model=ItemOut, status_code=201)
async def create_item(
    payload: ItemCreate,
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot create items")

    data = payload.model_dump()

    # Форма склада настраиваемая: значения своих полей приходят вперемешку с
    # колонками, разделяем их и заодно проверяем то, что магазин пометил
    # обязательным. Проверять на клиенте мало: правила живут на сервере.
    form = await fields_svc.ensure_defaults(session, member.store_id)
    extra = fields_svc.filter_extra(data.pop("extra", None) or {}, form)
    data, from_body = fields_svc.split_payload(data, form)
    extra.update(from_body)
    gaps = fields_svc.missing_required(form, data, extra)
    if gaps:
        raise HTTPException(422, "Заполните: " + ", ".join(gaps))

    # Конвертация денег в базовую валюту склада (orig+база храним отдельно).
    base = await _store_base_currency(session, member.store_id)
    money_in = {k: data.pop(k, None) for k in MONEY_KEYS}
    money_kwargs = await _build_money(money_in, base)

    # Название необязательно: если его не ввели, собираем из бренда и
    # категории. Раньше пустое дозаполняла нейросеть по фото — она отложена.
    if not data["title"].strip():
        data["title"] = f"{data['brand']} {data['category']}".strip()[:100]

    repo = ItemRepository(session)
    sku = await repo.next_sku(member.store_id)
    item = Item(
        store_id=member.store_id,
        sku=sku,
        purchaser_id=user.id,
        extra=extra,
        **data,
        **money_kwargs,
    )
    session.add(item)
    await session.flush()  # нужен item.id для ссылки в журнале
    audit.record(
        session,
        store_id=member.store_id,
        user_id=user.id,
        action=audit.ITEM_CREATE,
        summary=f"{sku} {item.title or ''}".strip(),
        entity_type="item",
        entity_id=item.id,
    )
    await session.commit()
    await session.refresh(item)
    # Здесь запускалась фоновая генерация названия и описания по фото. Она
    # отложена вместе с остальной нейросетью — см. _parked/README.md.
    return to_out(item, show_finance=_can_see_finance(member))


@router.patch("/{item_id}/status", response_model=ItemOut)
async def patch_status(
    item_id: uuid.UUID,
    payload: StatusPatch,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot change status")

    scope = f"status:{item_id}"
    cached = await idempotency.get_cached(scope, idempotency_key)
    if cached is not None:
        return ItemOut(**cached)

    repo = ItemRepository(session)
    item = await repo.get(member.store_id, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")

    target = payload.target_status or next_status(item.status)
    if target is None:
        raise HTTPException(409, "No forward status from current state")
    if not can_transition(item.status, target):
        raise HTTPException(
            409, f"Transition {item.status.value}->{target.value} not allowed"
        )
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one_or_none()
    base = (store.base_currency if store else "BYN").upper()

    # В канал не выпускаем вещь без ценника: пост с «Цена — в личные
    # сообщения» вместо суммы обесценивает витрину, а покупатель уходит.
    if target == ItemStatus.LISTED and item.list_price_orig is None:
        raise HTTPException(
            422, "Укажите цену продажи — без неё вещь нельзя выставить в канал"
        )

    # Цена продажи с валютой -> база. Если не передана — берём list_price/прежнюю.
    sell_base = sell_orig = sell_cur = None
    if target == ItemStatus.SHIPPED:
        sell_cur = (payload.selling_currency or item.price_currency or base).upper()
        if payload.selling_price is not None:
            sell_orig = payload.selling_price
        elif item.selling_price_orig is not None:
            sell_orig, sell_cur = item.selling_price_orig, item.price_currency
        elif item.list_price_orig is not None:
            sell_orig, sell_cur = item.list_price_orig, item.price_currency
        if sell_orig is None:
            raise HTTPException(422, "Для отправки нужна цена продажи")
        sell_base = await fx.convert(sell_orig, sell_cur, base)

    ok = await repo.apply_status(
        item,
        target,
        expected_version=payload.version,
        changed_by=user.id,
        selling_price=sell_base,
        selling_orig=sell_orig,
        sell_currency=sell_cur,
    )
    if not ok:
        await session.rollback()
        raise HTTPException(409, "Version conflict — reload item")

    await session.commit()
    fresh = await repo.get(member.store_id, item_id)
    out = to_out(fresh, show_finance=_can_see_finance(member))
    await idempotency.store_result(scope, idempotency_key, out.model_dump(mode="json"))
    return out


@router.delete("/{item_id}", status_code=204)
async def delete_item(
    item_id: uuid.UUID,
    hard: bool = Query(False, description="true — удалить безвозвратно, иначе в архив"),
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    """hard=false — архивация (soft-delete); hard=true — безвозвратное удаление."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot delete")
    repo = ItemRepository(session)
    item = await repo.get(member.store_id, item_id, include_archived=True)
    if item is None:
        raise HTTPException(404, "Item not found")
    # Подпись собираем до удаления: после hard_delete у объекта не остаётся
    # ни артикула, ни названия, и в журнале был бы безымянный «удалил вещь».
    label = f"{item.sku or ''} {item.title or ''}".strip()
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.ITEM_DELETE,
        summary=label + (" · безвозвратно" if hard else " · в архив"),
        entity_type="item",
        entity_id=None if hard else item.id,
    )
    if hard:
        await repo.hard_delete(item)
    else:
        await repo.archive(item)
    await session.commit()
