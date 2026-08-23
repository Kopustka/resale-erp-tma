"""Шаблоны подписей для автопостинга: CRUD, превью, клонирование дизайна."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import OWNER_ONLY, get_active_membership, get_current_user, require_role
from ..db import get_session
from ..models import PostTemplate, StoreMember, User
from ..schemas import (
    CaptureSessionOut,
    CaptureStatusOut,
    PostTemplateCreate,
    PostTemplateOut,
    PostTemplateUpdate,
    TemplateBriefIn,
    TemplateGenerated,
    TemplatePlaceholder,
    TemplatePreviewIn,
    TemplatePreviewOut,
)
from ..services import ai_template
from ..services import audit
from ..services import post_template as pt
from ..services import template_capture
from ..services.ai_describe import AiGenerationError, AiNotConfigured
from ..services.telegram_post import get_bot_username

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


async def _get_owned(
    session: AsyncSession, store_id: uuid.UUID, template_id: uuid.UUID
) -> PostTemplate:
    tpl = (
        await session.execute(
            select(PostTemplate).where(
                PostTemplate.id == template_id, PostTemplate.store_id == store_id
            )
        )
    ).scalar_one_or_none()
    if tpl is None:
        raise HTTPException(404, "Template not found")
    return tpl


async def _clear_default(session: AsyncSession, store_id: uuid.UUID) -> None:
    await session.execute(
        update(PostTemplate)
        .where(PostTemplate.store_id == store_id, PostTemplate.is_default.is_(True))
        .values(is_default=False)
        .execution_options(synchronize_session=False)
    )


# --------------------------------------------------------------------------- #
# Справочники и превью (объявлены до /{template_id}, иначе перехватит он)
# --------------------------------------------------------------------------- #
@router.get("/placeholders", response_model=list[TemplatePlaceholder])
async def list_placeholders(_: StoreMember = Depends(require_role(*OWNER_ONLY))):
    return pt.PLACEHOLDERS


@router.post("/preview", response_model=TemplatePreviewOut)
async def preview(
    payload: TemplatePreviewIn,
    _: StoreMember = Depends(require_role(*OWNER_ONLY)),
):
    """Рендер шаблона на демо-вещи — живой предпросмотр в редакторе."""
    try:
        pt.validate_body(payload.body)
    except pt.TemplateError as e:
        raise HTTPException(422, str(e))
    return TemplatePreviewOut(caption=pt.render_demo(payload.body))


@router.post("/generate", response_model=TemplateGenerated)
async def generate(
    payload: TemplateBriefIn,
    _: StoreMember = Depends(require_role(*OWNER_ONLY)),
):
    """Собирает шаблон по словесному описанию. Не сохраняет — только предлагает."""
    try:
        result = await ai_template.generate_template_from_brief(payload.brief)
    except AiNotConfigured:
        raise HTTPException(503, "AI-генерация не настроена: не задан GEMINI_API_KEY")
    except AiGenerationError as e:
        raise HTTPException(502, str(e))
    return TemplateGenerated(**result)


# --------------------------------------------------------------------------- #
# Захват примера поста через бота
# --------------------------------------------------------------------------- #
@router.post("/capture", response_model=CaptureSessionOut)
async def start_capture(
    user: User = Depends(get_current_user),
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
):
    """Готовит сессию и ссылку в чат с ботом, куда слать пример поста."""
    username = await get_bot_username()
    if not username:
        raise HTTPException(503, "Не удалось определить бота — проверьте BOT_TOKEN")
    token = await template_capture.create(user.telegram_id, member.store_id)
    return CaptureSessionOut(
        token=token,
        deep_link=f"https://t.me/{username}?start=tpl_{token}",
        expires_in=template_capture.TTL,
    )


@router.get("/capture/{token}", response_model=CaptureStatusOut)
async def capture_status(
    token: str,
    user: User = Depends(get_current_user),
    _: StoreMember = Depends(require_role(*OWNER_ONLY)),
):
    data = await template_capture.get(token)
    if data is None:
        return CaptureStatusOut(status="expired")
    if data["telegram_id"] != user.telegram_id:
        raise HTTPException(404, "Session not found")
    tpl_id = data.get("template_id")
    return CaptureStatusOut(
        status=data["status"],
        template_id=uuid.UUID(tpl_id) if tpl_id else None,
        error=data.get("error"),
    )


@router.delete("/capture/{token}", status_code=204)
async def cancel_capture(
    token: str,
    user: User = Depends(get_current_user),
    _: StoreMember = Depends(require_role(*OWNER_ONLY)),
):
    data = await template_capture.get(token)
    if data is not None and data["telegram_id"] == user.telegram_id:
        await template_capture.cancel(token)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[PostTemplateOut])
async def list_templates(
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(PostTemplate)
            .where(PostTemplate.store_id == member.store_id)
            .order_by(PostTemplate.is_default.desc(), PostTemplate.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


@router.post("", response_model=PostTemplateOut, status_code=201)
async def create_template(
    payload: PostTemplateCreate,
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    try:
        pt.validate_body(payload.body)
    except pt.TemplateError as e:
        raise HTTPException(422, str(e))

    has_any = (
        await session.execute(
            select(PostTemplate.id).where(PostTemplate.store_id == member.store_id).limit(1)
        )
    ).scalar_one_or_none()

    tpl = PostTemplate(
        store_id=member.store_id,
        name=payload.name.strip(),
        body=payload.body,
        is_default=has_any is None,  # первый шаблон сразу становится активным
    )
    session.add(tpl)
    await session.flush()
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.TEMPLATE_CREATE,
        summary=tpl.name,
        entity_type="template",
        entity_id=tpl.id,
    )
    await session.commit()
    await session.refresh(tpl)
    return tpl


@router.patch("/{template_id}", response_model=PostTemplateOut)
async def update_template(
    template_id: uuid.UUID,
    payload: PostTemplateUpdate,
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    tpl = await _get_owned(session, member.store_id, template_id)
    if payload.body is not None:
        try:
            pt.validate_body(payload.body)
        except pt.TemplateError as e:
            raise HTTPException(422, str(e))
        tpl.body = payload.body
    if payload.name is not None:
        tpl.name = payload.name.strip()
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.TEMPLATE_EDIT,
        summary=tpl.name,
        entity_type="template",
        entity_id=tpl.id,
    )
    await session.commit()
    await session.refresh(tpl)
    return tpl


@router.post("/{template_id}/default", response_model=PostTemplateOut)
async def make_default(
    template_id: uuid.UUID,
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    tpl = await _get_owned(session, member.store_id, template_id)
    await _clear_default(session, member.store_id)
    tpl.is_default = True
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.TEMPLATE_DEFAULT,
        summary=tpl.name,
        entity_type="template",
        entity_id=tpl.id,
    )
    await session.commit()
    await session.refresh(tpl)
    return tpl


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: uuid.UUID,
    member: StoreMember = Depends(get_active_membership),
    __: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    tpl = await _get_owned(session, member.store_id, template_id)
    was_default = tpl.is_default
    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.TEMPLATE_DELETE,
        summary=tpl.name,
        entity_type="template",
        entity_id=None,
    )
    await session.delete(tpl)
    await session.flush()

    # Склад не должен остаться без активного шаблона: назначаем следующий.
    if was_default:
        nxt = (
            await session.execute(
                select(PostTemplate)
                .where(PostTemplate.store_id == member.store_id)
                .order_by(PostTemplate.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if nxt is not None:
            nxt.is_default = True
    await session.commit()
