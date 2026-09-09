"""Идемпотентность смены статуса через Redis.

Клиент шлёт Idempotency-Key на каждый жест. Повтор того же ключа
возвращает первый результат, не применяя переход повторно.
"""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from ..config import get_settings

settings = get_settings()
_redis: aioredis.Redis | None = None

_TTL = 60 * 10  # 10 минут — окно защиты от повторного нажатия
#: Метка «выполняется прямо сейчас». Кладётся до начала работы, поэтому
#: второй такой же запрос не проскочит мимо проверки и не получит вместо
#: кэша конфликт версий.
_RUNNING = "__running__"
_RESERVE_TTL = 60


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _key(scope: str, idem_key: str) -> str:
    return f"idem:{scope}:{idem_key}"


async def get_cached(scope: str, idem_key: str) -> dict | None:
    raw = await get_redis().get(_key(scope, idem_key))
    if not raw or raw == _RUNNING:
        return None
    return json.loads(raw)


async def reserve(scope: str, idem_key: str) -> bool:
    """Занять ключ. False — тем же ключом уже кто-то работает.

    Одной проверки кэша мало: два нажатия подряд успевали пройти её оба,
    первое применяло переход, второе получало 409 про конфликт версий —
    ровно то, от чего идемпотентность и защищает.
    """
    return bool(
        await get_redis().set(
            _key(scope, idem_key), _RUNNING, nx=True, ex=_RESERVE_TTL
        )
    )


async def release(scope: str, idem_key: str) -> None:
    """Снять занятость, не оставив ключ занятым после ошибки."""
    redis = get_redis()
    key = _key(scope, idem_key)
    if await redis.get(key) == _RUNNING:
        await redis.delete(key)


async def store_result(scope: str, idem_key: str, payload: dict) -> None:
    await get_redis().set(_key(scope, idem_key), json.dumps(payload, default=str), ex=_TTL)
