"""Точка входа FastAPI."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import Base, engine
from .routers import analytics, items, media, stores

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Для MVP создаём таблицы напрямую; в проде — Alembic.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
    return JSONResponse(
        status_code=500,
        content={"code": "internal_error", "message": str(exc), "details": None},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


app.include_router(items.router)
app.include_router(analytics.router)
app.include_router(stores.router)
app.include_router(media.router)
