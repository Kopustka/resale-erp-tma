"""Идемпотентность свайпов через Redis.

Клиент шлёт Idempotency-Key на каждый жест. Повтор того же ключа
возвращает первый результат, не применяя переход повторно.
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from ..config import get_settings

settings = get_settings()
_redis: aioredis.Redis | None = None

_TTL = 60 * 10  # 10 минут — окно защиты от дабл-свайпа


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _key(scope: str, idem_key: str) -> str:
    return f"idem:{scope}:{idem_key}"


async def get_cached(scope: str, idem_key: str) -> dict | None:
    raw = await get_redis().get(_key(scope, idem_key))
    return json.loads(raw) if raw else None


async def store_result(scope: str, idem_key: str, payload: dict) -> None:
    await get_redis().set(_key(scope, idem_key), json.dumps(payload, default=str), ex=_TTL)
