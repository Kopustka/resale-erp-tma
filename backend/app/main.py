"""Точка входа FastAPI."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .config import get_settings
from .db import Base, engine
from .routers import analytics, items, media, stores, templates

settings = get_settings()
log = logging.getLogger("api")


# Alembic в проекте нет, а create_all умеет только создавать таблицы —
# добавить колонку в существующую он не может. Пока мигратора не завели,
# держим здесь идемпотентные ALTER: они безопасны при каждом старте.
_ENSURE_COLUMNS = (
    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS watermark_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE stores ADD COLUMN IF NOT EXISTS watermark_text VARCHAR(60)",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _ENSURE_COLUMNS:
            await conn.execute(text(stmt))
    yield


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
