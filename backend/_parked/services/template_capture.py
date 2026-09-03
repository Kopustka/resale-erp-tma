"""Сессии «пришли боту пример поста» — мост между мини-аппом и ботом.

Поток: мини-апп создаёт сессию -> открывает t.me/<bot>?start=tpl_<token> ->
бот по deep link «взводит» сессию -> следующее сообщение пользователя
считается примером -> бот кладёт результат в сессию -> мини-апп забирает
его поллингом.

Живёт в Redis, а не в БД: состояние короткое и одноразовое, TTL решает
задачу протухания сам.
"""
from __future__ import annotations

import json
import secrets
import uuid

from .idempotency import get_redis

TTL = 15 * 60  # 15 минут на то, чтобы дойти до чата и переслать пост

WAITING = "waiting"    # сессия создана, юзер ещё не нажал /start
ARMED = "armed"        # бот принял deep link и ждёт пример
DONE = "done"
ERROR = "error"


def _skey(token: str) -> str:
    return f"tplcap:token:{token}"


def _ukey(telegram_id: int) -> str:
    return f"tplcap:user:{telegram_id}"


async def create(telegram_id: int, store_id: uuid.UUID) -> str:
    """Создаёт сессию, возвращает токен для deep link."""
    token = secrets.token_urlsafe(16)
    payload = {
        "status": WAITING,
        "telegram_id": telegram_id,
        "store_id": str(store_id),
        "template_id": None,
        "error": None,
    }
    r = get_redis()
    await r.set(_skey(token), json.dumps(payload), ex=TTL)
    # Обратный индекс: бот знает только телеграм-юзера, но не токен.
    await r.set(_ukey(telegram_id), token, ex=TTL)
    return token


async def get(token: str) -> dict | None:
    raw = await get_redis().get(_skey(token))
    return json.loads(raw) if raw else None


async def get_active_for_user(telegram_id: int) -> tuple[str, dict] | None:
    """Активная сессия юзера, если она есть и ещё жива."""
    r = get_redis()
    token = await r.get(_ukey(telegram_id))
    if not token:
        return None
    data = await get(token)
    if data is None:
        return None
    return token, data


async def _save(token: str, data: dict) -> None:
    # Сохраняем остаток TTL, чтобы обновление не продлевало сессию бесконечно.
    r = get_redis()
    ttl = await r.ttl(_skey(token))
    await r.set(_skey(token), json.dumps(data), ex=ttl if ttl and ttl > 0 else TTL)


async def arm(token: str) -> dict | None:
    """Бот получил deep link — переводим сессию в ожидание примера."""
    data = await get(token)
    if data is None or data["status"] in (DONE, ERROR):
        return None
    data["status"] = ARMED
    await _save(token, data)
    return data


async def finish(token: str, template_id: uuid.UUID) -> None:
    data = await get(token)
    if data is None:
        return
    data["status"] = DONE
    data["template_id"] = str(template_id)
    await _save(token, data)
    await get_redis().delete(_ukey(data["telegram_id"]))


async def fail(token: str, message: str) -> None:
    data = await get(token)
    if data is None:
        return
    data["status"] = ERROR
    data["error"] = message
    await _save(token, data)
    await get_redis().delete(_ukey(data["telegram_id"]))


async def cancel(token: str) -> None:
    data = await get(token)
    if data is not None:
        await get_redis().delete(_ukey(data["telegram_id"]))
    await get_redis().delete(_skey(token))
