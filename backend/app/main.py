"""Точка входа FastAPI."""
from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import get_settings
from .db import Base, engine
from .routers import (
    analytics,
    channels,
    drops,
    items,
    media,
    posts,
    stores,
    templates,
)

settings = get_settings()
log = logging.getLogger("api")


# Alembic в проекте нет, а create_all умеет только создавать таблицы —
# добавить колонку в существующую он не может. Пока мигратора не завели,
# держим здесь идемпотентные ALTER: они безопасны при каждом старте.
_ENSURE_COLUMNS = (
    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS watermark_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS watermark_text VARCHAR(60)",
    "ALTER TABLE post_jobs ADD COLUMN IF NOT EXISTS channel_uid UUID",
    "ALTER TABLE post_jobs ADD COLUMN IF NOT EXISTS caption_prefix VARCHAR(64)",
    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS bump_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS bump_after_days INTEGER NOT NULL DEFAULT 60",
    "ALTER TABLE items ADD COLUMN IF NOT EXISTS bumped_at TIMESTAMPTZ",
    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS preview_before_post BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE item_posts ADD COLUMN IF NOT EXISTS reactions INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS subscriptions_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE post_jobs ADD COLUMN IF NOT EXISTS sub_id UUID",
    "ALTER TABLE post_jobs ADD COLUMN IF NOT EXISTS drop_id UUID",
    "ALTER TABLE post_jobs ADD COLUMN IF NOT EXISTS custom_post_id UUID",
    "ALTER TABLE item_posts ADD COLUMN IF NOT EXISTS discussion_chat_id BIGINT",
    "ALTER TABLE item_posts ADD COLUMN IF NOT EXISTS discussion_message_id BIGINT",
    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS auto_reply_enabled BOOLEAN NOT NULL DEFAULT FALSE",
)

# Значения enum'ов: create_all создаёт тип при первом запуске, но новые
# значения в существующий не добавляет. ALTER TYPE ... ADD VALUE нельзя
# выполнять внутри транзакции, поэтому эти идут отдельно, в автокоммите.
_ENSURE_ENUM_VALUES = (
    "ALTER TYPE job_kind_enum ADD VALUE IF NOT EXISTS 'EDIT_CAPTION'",
    "ALTER TYPE job_kind_enum ADD VALUE IF NOT EXISTS 'BUMP'",
    "ALTER TYPE job_kind_enum ADD VALUE IF NOT EXISTS 'NOTIFY_SUB'",
    "ALTER TYPE job_kind_enum ADD VALUE IF NOT EXISTS 'DROP_POST'",
    "ALTER TYPE job_kind_enum ADD VALUE IF NOT EXISTS 'CUSTOM_POST'",
    "ALTER TYPE job_status_enum ADD VALUE IF NOT EXISTS 'AWAITING'",
)

# Перенос на мультиканальность. Оба шага идемпотентны (NOT EXISTS + ON CONFLICT),
# поэтому безопасно выполняются при каждом старте, а не один раз.
_BACKFILL = (
    # 1. Настроенный канал склада -> строка в channels.
    """
    INSERT INTO channels (store_id, chat_id, signature, enabled)
    SELECT s.id, s.channel_id, s.channel_signature, TRUE
    FROM stores s
    WHERE s.channel_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM channels c WHERE c.store_id = s.id AND c.chat_id = s.channel_id
      )
    """,
    # Схлопывание статусов до пяти. Значения из enum PostgreSQL удалить
    # нельзя, поэтому переносим строки: «продан» и «завершён» становятся
    # «отправлен», бронь и отмена возвращаются в «выставлен», возврат — тоже.
    "UPDATE items SET status='SHIPPED' WHERE status IN ('SOLD','COMPLETED')",
    "UPDATE items SET status='LISTED' WHERE status IN ('BOOKED','CANCELLED','RETURNED')",
    "UPDATE item_status_logs SET new_status='SHIPPED' WHERE new_status IN ('SOLD','COMPLETED')",
    "UPDATE item_status_logs SET new_status='LISTED' WHERE new_status IN ('BOOKED','CANCELLED','RETURNED')",
    "UPDATE item_status_logs SET old_status='SHIPPED' WHERE old_status IN ('SOLD','COMPLETED')",
    "UPDATE item_status_logs SET old_status='LISTED' WHERE old_status IN ('BOOKED','CANCELLED','RETURNED')",
    # 2. Уже опубликованные посты -> item_posts, чтобы пометка «продано»
    #    и защита от повторной публикации продолжали работать.
    """
    INSERT INTO item_posts (item_id, channel_id, message_id, sold_marked)
    SELECT i.id, c.id, i.channel_message_id, FALSE
    FROM items i
    JOIN channels c ON c.store_id = i.store_id
    WHERE i.channel_message_id IS NOT NULL
    ON CONFLICT ON CONSTRAINT uq_post_item_channel DO NOTHING
    """,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _ENSURE_COLUMNS:
            await conn.execute(text(stmt))
        for stmt in _BACKFILL:
            await conn.execute(text(stmt))

    async with engine.connect() as conn:
        auto = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for stmt in _ENSURE_ENUM_VALUES:
            await auto.execute(text(stmt))

    from .services.post_worker import run_forever

    worker = asyncio.create_task(run_forever(), name="post-queue")
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker


app = FastAPI(title="Resale ERP TMA", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Наружу — обезличенно, подробности только в журнал.

    str(exc) отдавать нельзя: SQLAlchemy дописывает в текст ошибки блоки
    [SQL: ...] и [parameters: ...], то есть схему таблиц и значения полей —
    в том числе закупочные цены, которые роль EMPLOYEE видеть не должна.
    trace_id связывает ответ клиента с записью в journalctl.
    """
    trace_id = uuid.uuid4().hex[:12]
    log.exception(
        "unhandled error trace_id=%s %s %s", trace_id, request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "message": "Внутренняя ошибка сервера",
            "details": {"trace_id": trace_id},
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(items.router)
app.include_router(analytics.router)
app.include_router(stores.router)
app.include_router(media.router)
app.include_router(templates.router)
app.include_router(channels.router)
app.include_router(drops.router)
app.include_router(posts.router)
