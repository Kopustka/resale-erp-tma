"""Медиа: загрузка фото из галереи (локальное хранилище) + отдача.

Отдаётся через прокси GET /api/v1/media/{item_id}/{index}:
- запись вида "local:<name>" — файл из локального хранилища (галерея);
- иначе — Telegram file_id (фото пришло через бота).
Фронтенд рендерит <img src>, поэтому file_id нельзя отдать напрямую.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_active_membership
from ..config import get_settings
from ..db import get_session
from ..models import Item, Role, StoreMember

router = APIRouter(prefix="/api/v1/media", tags=["media"])
settings = get_settings()

MEDIA_DIR = Path(settings.media_dir)
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_PREFIX = "local:"

# content-type -> расширение
ALLOWED_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
}


async def _fetch_telegram_file(file_id: str) -> tuple[bytes, str]:
    base = f"https://api.telegram.org/bot{settings.bot_token}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{base}/getFile", params={"file_id": file_id})
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise HTTPException(404, "file not found in Telegram")
        file_path = data["result"]["file_path"]
        file_url = f"https://api.telegram.org/file/bot{settings.bot_token}/{file_path}"
        fr = await client.get(file_url)
        fr.raise_for_status()
        ctype = fr.headers.get("content-type", "image/jpeg")
        return fr.content, ctype


@router.post("/upload")
async def upload_media(
    file: UploadFile = File(...),
    member: StoreMember = Depends(get_active_membership),
):
    """Приём фото из галереи/камеры. Возвращает photo_id вида 'local:<name>'."""
    if member.role not in (Role.OWNER, Role.EMPLOYEE):
        raise HTTPException(403, "Role cannot upload photos")

    ext = ALLOWED_TYPES.get((file.content_type or "").lower())
    if ext is None:
        raise HTTPException(422, "Unsupported image type")

    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB")
    if not data:
        raise HTTPException(422, "Empty file")

    name = f"{uuid.uuid4().hex}.{ext}"
    (MEDIA_DIR / name).write_bytes(data)
    return {"photo_id": f"{LOCAL_PREFIX}{name}"}


def _serve_local(entry: str) -> FileResponse:
    name = entry[len(LOCAL_PREFIX):]
    # защита от path traversal
    if "/" in name or "\\" in name or ".." in name:
        raise HTTPException(400, "Bad media name")
    path = MEDIA_DIR / name
    if not path.exists():
        raise HTTPException(404, "Media file missing")
    return FileResponse(
        path,
        headers={"Cache-Control": f"public, max-age={settings.media_cache_ttl}, immutable"},
    )


@router.get("/{item_id}/{index}")
async def get_media(
    item_id: uuid.UUID,
    index: int,
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    item = (
        await session.execute(
            select(Item).where(Item.id == item_id, Item.store_id == member.store_id)
        )
    ).scalar_one_or_none()
    if item is None or index >= len(item.photo_file_ids or []):
        raise HTTPException(404, "Photo not found")

    entry = item.photo_file_ids[index]
    if entry.startswith(LOCAL_PREFIX):
        return _serve_local(entry)

    # Telegram file_id
    content, ctype = await _fetch_telegram_file(entry)
    return Response(
        content=content,
        media_type=ctype,
        headers={"Cache-Control": f"public, max-age={settings.media_cache_ttl}, immutable"},
    )
