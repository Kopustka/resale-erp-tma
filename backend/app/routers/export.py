"""Выгрузка склада файлом.

Доступ — тем же ролям, что видят аналитику. Просмотр списка в приложении и
выгрузка всего склада одним файлом — разные вещи: первое сотруднику нужно
для работы, второе уносится целиком и к работе не относится.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import CAN_SEE_FINANCE, require_role
from ..db import get_session
from ..models import StoreMember
from ..schemas import ExportRequest, ExportResult
from ..services import audit
from ..services import export as svc

router = APIRouter(prefix="/api/v1/export", tags=["export"])


@router.post("/items", response_model=ExportResult)
async def export_items(
    payload: ExportRequest,
    member: StoreMember = Depends(require_role(*CAN_SEE_FINANCE)),
    session: AsyncSession = Depends(get_session),
):
    """Собрать CSV и прислать его файлом в чат с ботом."""
    chat_id = await svc.chat_id_of(session, member.user_id)
    if chat_id is None:
        raise HTTPException(404, "Не нашли, кому отправить файл")

    name, data, count = await svc.build_items_csv(
        session,
        member.store_id,
        show_finance=True,
        include_archived=payload.include_archived,
    )

    sent = await svc.send_document(
        chat_id,
        name,
        data,
        caption=(
            f"📄 Выгрузка склада — {count} вещей.\n"
            "Открывается в Excel и Google Таблицах."
        ),
    )
    if not sent:
        # Частая причина — человек ни разу не писал боту, и личку открыть
        # некому. Говорим прямо, что делать.
        raise HTTPException(
            502,
            "Не удалось отправить файл. Напишите боту /start и попробуйте снова",
        )

    audit.record(
        session,
        store_id=member.store_id,
        user_id=member.user_id,
        action=audit.DATA_EXPORT,
        summary=f"выгрузка склада · вещей {count}"
        + (" · с архивом" if payload.include_archived else ""),
    )
    await session.commit()
    return ExportResult(filename=name, rows=count, bytes=len(data))
