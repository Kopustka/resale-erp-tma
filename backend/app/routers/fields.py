"""Настройка формы вещи: показать/скрыть, переименовать, переставить, добавить.

Читать набор может любой участник — форма нужна всем, кто заводит вещи.
Менять его вправе только владелец: это настройка склада, а не личная.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import OWNER_ONLY, get_active_membership, require_role
from ..db import get_session
from ..models import FieldKind, StoreField, StoreMember
from ..schemas import FieldCreate, FieldOut, FieldsUpdate
from ..services import audit
from ..services import fields as svc

router = APIRouter(prefix="/api/v1/fields", tags=["fields"])

MAX_FIELDS = 40


def _out(f: StoreField) -> FieldOut:
    return FieldOut(
        id=f.id,
        key=f.key,
        label=f.label,
        kind=f.kind.value,
        enabled=f.enabled,
        required=f.required,
        position=f.position,
        builtin=f.builtin,
        options=list(f.options or []),
        hint=f.hint,
        locked=f.key in svc.LOCKED_KEYS,
        required_locked=f.key in svc.REQUIRED_KEYS,
    )


@router.get("", response_model=list[FieldOut])
async def list_fields(
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    """Набор полей склада. При первом обращении создаётся стандартный."""
    rows = await svc.ensure_defaults(session, member.store_id)
    return [_out(f) for f in rows]


@router.put("", response_model=list[FieldOut])
async def update_fields(
    payload: FieldsUpdate,
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    """Пакетная правка: порядок, видимость, названия, обязательность."""
    rows = await svc.ensure_defaults(session, member.store_id)
    by_id = {f.id: f for f in rows}
    moved: set = set()

    for patch in payload.fields:
        f = by_id.get(patch.id)
        if f is None:
            raise HTTPException(404, "Поле не найдено")

        if patch.label is not None:
            f.label = patch.label.strip()
        if patch.position is not None:
            f.position = patch.position
            moved.add(f.id)
        if patch.hint is not None:
            f.hint = patch.hint.strip() or None
        if patch.options is not None:
            f.options = [o.strip() for o in patch.options if o.strip()][:20]

        # Название вещи собирается из бренда и категории, поэтому убрать их
        # из формы нельзя. Обязательность — отдельный вопрос: категорию без
        # неё не отличить, а бренд у вещи может просто отсутствовать.
        if patch.enabled is not None:
            if f.key in svc.LOCKED_KEYS and not patch.enabled:
                raise HTTPException(422, f"«{f.label}» нельзя скрыть")
            f.enabled = patch.enabled
        if patch.required is not None:
            if f.key in svc.REQUIRED_KEYS and not patch.required:
                raise HTTPException(422, f"«{f.label}» всегда обязательно")
            f.required = patch.required

    # Перенумеровываем подряд. Клиент присылает позицию только для тех полей,
    # которые двигал, и она может совпасть с чужой. Разрешаем спор в пользу
    # передвинутого: человек указал место явно, а сосед просто там оказался.
    # Время создания как последний признак не годится — у полей из набора по
    # умолчанию оно одинаковое, и порядок скакал бы между загрузками.
    for pos, f in enumerate(
        sorted(rows, key=lambda x: (x.position, 0 if x.id in moved else 1, x.key))
    ):
        f.position = pos

    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.SETTINGS_EDIT,
        summary=f"форма вещи: изменено полей {len(payload.fields)}",
    )
    await session.commit()
    rows = await svc.ensure_defaults(session, member.store_id)
    return [_out(f) for f in rows]


@router.post("", response_model=FieldOut, status_code=201)
async def create_field(
    payload: FieldCreate,
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    """Своё поле. Колонки в базе не появляется — значение ляжет в items.extra."""
    rows = await svc.ensure_defaults(session, member.store_id)
    if len(rows) >= MAX_FIELDS:
        raise HTTPException(422, f"Больше {MAX_FIELDS} полей в форме не поместится")

    try:
        kind = FieldKind(payload.kind.upper())
    except ValueError:
        raise HTTPException(422, "Неизвестный тип поля")

    options = [o.strip() for o in payload.options if o.strip()][:20]
    if kind is FieldKind.SELECT and not options:
        raise HTTPException(422, "У поля с выбором нужен хотя бы один вариант")

    field = StoreField(
        store_id=member.store_id,
        key=svc.make_key(payload.label, {f.key for f in rows}),
        label=payload.label.strip(),
        kind=kind,
        required=payload.required,
        enabled=True,
        position=max((f.position for f in rows), default=0) + 1,
        builtin=False,
        options=options,
        hint=(payload.hint or "").strip() or None,
    )
    session.add(field)
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.SETTINGS_EDIT,
        summary=f"добавлено поле «{field.label}»",
    )
    await session.commit()
    await session.refresh(field)
    return _out(field)


@router.delete("/{field_id}", status_code=204)
async def delete_field(
    field_id: uuid.UUID,
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    """Удалить можно только своё поле. Встроенное — скрыть.

    Значения удалённого поля остаются в items.extra: если магазин передумает
    и заведёт поле с тем же ключом, данные вернутся. Чистить их молча было бы
    потерей того, что человек уже вписал.
    """
    f = (
        await session.execute(
            select(StoreField).where(
                StoreField.id == field_id, StoreField.store_id == member.store_id
            )
        )
    ).scalar_one_or_none()
    if f is None:
        raise HTTPException(404, "Поле не найдено")
    if f.builtin:
        raise HTTPException(422, "Встроенное поле можно только скрыть")

    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.SETTINGS_EDIT,
        summary=f"удалено поле «{f.label}»",
    )
    await session.delete(f)
    await session.commit()
