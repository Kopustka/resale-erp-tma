"""Роутер товаров: список (cursor), создание, свайп-статус, архив, подсказки."""
from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CAN_EDIT, get_active_membership, get_current_user
from ..db import SessionLocal, get_session
from ..models import Item, ItemStatus, PostTemplate, Role, Store, StoreMember, User
from ..repositories.items import ItemRepository
from ..config import get_settings
from ..schemas import (
    AiDescribeOut,
    ItemCreate,
    ItemOut,
    ItemPage,
    ItemUpdate,
    StatusPatch,
    VoiceParseRequest,
    VoiceParseResult,
)
from ..services import fx, idempotency, telegram_post
from ..services.ai_describe import (
    AiGenerationError,
    AiNotConfigured,
    generate_item_description,
)
from ..services.fsm import SOLD_STATUSES, can_transition, next_status
from ..services.voice_parser import parse_item_voice

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
    }
    # list_price (цена в объявлении) видна всем ролям — это публичный ценник.
    data.update(
        list_price=item.list_price_orig,
        list_price_base=item.list_price,
        price_currency=item.price_currency,
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


@router.post("/parse-voice", response_model=VoiceParseResult)
async def parse_voice(
    payload: VoiceParseRequest,
    member: StoreMember = Depends(get_active_membership),
):
    """Разбор голосовой фразы в поля новой вещи (для предзаполнения формы)."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot create items")
    p = parse_item_voice(payload.text)
    return VoiceParseResult(
        brand=p.brand,
        category=p.category,
        size=p.size,
        color=p.color,
        condition=p.condition,
        cost_price=p.cost_price,
        title=p.title,
        low_confidence=p.low_confidence,
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

    if data:
        await repo.update_fields(item, data)
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

    # Конвертация денег в базовую валюту склада (orig+база храним отдельно).
    base = await _store_base_currency(session, member.store_id)
    money_in = {k: data.pop(k, None) for k in MONEY_KEYS}
    money_kwargs = await _build_money(money_in, base)

    photos = data.get("photo_file_ids") or []
    need_title = not data["title"].strip()
    need_descr = not (data.get("description") or "").strip()
    if need_title:
        data["title"] = f"{data['brand']} {data['category']}".strip()[:100]

    repo = ItemRepository(session)
    sku = await repo.next_sku(member.store_id)
    item = Item(
        store_id=member.store_id,
        sku=sku,
        purchaser_id=user.id,
        **data,
        **money_kwargs,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)

    # AI-генерация названия/описания — В ФОНЕ: сохранение мгновенное,
    # поля дозаполнятся через несколько секунд (фронт подтянет).
    if settings.gemini_api_key and photos and (need_title or need_descr):
        asyncio.create_task(
            _bg_generate_description(
                item.id,
                photos,
                {
                    "brand": data.get("brand"),
                    "category": data.get("category"),
                    "size": data.get("size"),
                    "color": data.get("color"),
                    "condition": data.get("condition"),
                },
                need_title,
                need_descr,
            )
        )

    return to_out(item, show_finance=_can_see_finance(member))


async def _bg_generate_description(
    item_id: uuid.UUID,
    photos: list[str],
    fields: dict,
    need_title: bool,
    need_descr: bool,
) -> None:
    """Фоновая AI-генерация после создания вещи. Ошибки только логируем —
    вещь уже сохранена, перегенерировать можно из карточки."""
    try:
        gen = await generate_item_description(photos, fields)
    except (AiNotConfigured, AiGenerationError) as e:
        logging.getLogger("ai").warning("bg-generate %s failed: %s", item_id, e)
        return
    values: dict = {}
    if need_title:
        values["title"] = gen["title"]
    if need_descr:
        values["description"] = gen["description"]
    if not values:
        return
    # Версию НЕ трогаем: правка текстовых полей не должна ломать
    # optimistic lock параллельного свайпа.
    async with SessionLocal() as s:
        await s.execute(
            update(Item)
            .where(Item.id == item_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await s.commit()


@router.post("/{item_id}/ai-describe", response_model=AiDescribeOut)
async def ai_describe(
    item_id: uuid.UUID,
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    """Перегенерация названия/описания по фото вещи. НЕ сохраняет — фронт
    подставляет результат в форму, юзер правит и сохраняет сам."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot use AI describe")
    repo = ItemRepository(session)
    item = await repo.get(member.store_id, item_id, include_archived=True)
    if item is None:
        raise HTTPException(404, "Item not found")
    if not item.photo_file_ids:
        raise HTTPException(422, "У вещи нет фото — добавьте хотя бы одно")
    try:
        gen = await generate_item_description(
            item.photo_file_ids,
            {
                "brand": item.brand,
                "category": item.category,
                "size": item.size,
                "color": item.color,
                "condition": item.condition,
            },
        )
    except AiNotConfigured:
        raise HTTPException(503, "AI не настроен: добавьте GEMINI_API_KEY на сервере")
    except AiGenerationError as e:
        raise HTTPException(502, str(e))
    return AiDescribeOut(**gen)


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

    # Цена продажи с валютой -> база. Если не передана — берём list_price/прежнюю.
    sell_base = sell_orig = sell_cur = None
    if target == ItemStatus.SOLD:
        sell_cur = (payload.selling_currency or item.price_currency or base).upper()
        if payload.selling_price is not None:
            sell_orig = payload.selling_price
        elif item.selling_price_orig is not None:
            sell_orig, sell_cur = item.selling_price_orig, item.price_currency
        elif item.list_price_orig is not None:
            sell_orig, sell_cur = item.list_price_orig, item.price_currency
        if sell_orig is None:
            raise HTTPException(422, "selling_price required for SOLD")
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

    # Автопостинг в канал (в фоне): при выставлении — публикуем, при продаже — метим.
    channel = store.channel_id if store else None
    signature = store.channel_signature if store else None
    if channel:
        if target == ItemStatus.LISTED and fresh.channel_message_id is None:
            asyncio.create_task(_bg_post_to_channel(channel, item_id, signature))
        elif target in SOLD_STATUSES and fresh.channel_message_id is not None:
            asyncio.create_task(
                _bg_mark_sold(channel, item_id, fresh.channel_message_id, signature)
            )

    out = to_out(fresh, show_finance=_can_see_finance(member))
    await idempotency.store_result(scope, idempotency_key, out.model_dump(mode="json"))
    return out


def _item_to_post_dict(it: Item) -> dict:
    # Цена в объявлении: list_price, иначе фактическая цена продажи (в валюте продажи).
    price = it.list_price_orig if it.list_price_orig is not None else it.selling_price_orig
    return {
        "title": it.title,
        "description": it.description,
        "brand": it.brand,
        "category": it.category,
        "size": it.size,
        "color": it.color,
        "condition": it.condition,
        "sku": it.sku,
        "length_cm": it.length_cm,
        "width_cm": it.width_cm,
        "sleeve_cm": it.sleeve_cm,
        "price": price,
        "price_currency": it.price_currency,
        "photo_file_ids": list(it.photo_file_ids or []),
    }


async def _default_template_body(session, store_id: uuid.UUID) -> str | None:
    """Тело активного шаблона склада. None — встроенное оформление."""
    return (
        await session.execute(
            select(PostTemplate.body).where(
                PostTemplate.store_id == store_id, PostTemplate.is_default.is_(True)
            )
        )
    ).scalar_one_or_none()


async def _bg_post_to_channel(channel_id: str, item_id: uuid.UUID, signature: str | None) -> None:
    """Фоновая публикация вещи в канал + сохранение message_id (для дедупа)."""
    try:
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()
            if it is None or it.channel_message_id is not None:
                return
            post = _item_to_post_dict(it)
            tpl = await _default_template_body(s, it.store_id)
        msg_id = await telegram_post.post_item(channel_id, post, signature, tpl)
        if msg_id is not None:
            async with SessionLocal() as s:
                await s.execute(
                    update(Item)
                    .where(Item.id == item_id, Item.channel_message_id.is_(None))
                    .values(channel_message_id=msg_id)
                    .execution_options(synchronize_session=False)
                )
                await s.commit()
    except Exception as e:  # noqa: BLE001
        logging.getLogger("channel").warning("post %s failed: %s", item_id, e)


async def _bg_mark_sold(
    channel_id: str, item_id: uuid.UUID, message_id: int, signature: str | None
) -> None:
    try:
        async with SessionLocal() as s:
            it = (await s.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()
            if it is None:
                return
            post = _item_to_post_dict(it)
            tpl = await _default_template_body(s, it.store_id)
        await telegram_post.mark_sold(channel_id, message_id, post, signature, tpl)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("channel").warning("mark_sold %s failed: %s", item_id, e)


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
    if hard:
        await repo.hard_delete(item)
    else:
        await repo.archive(item)
    await session.commit()
