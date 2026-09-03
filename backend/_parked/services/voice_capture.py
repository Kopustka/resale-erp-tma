"""Сессии «надиктуй боту» — мост между мини-аппом и голосовым сообщением.

Тот же приём, что и у копирования дизайна поста: мини-апп заводит сессию,
открывает чат с ботом, пользователь записывает голосовое штатной кнопкой
Telegram, бот отдаёт запись модели и кладёт разобранные поля в сессию.

Почему через бота, а не в самом мини-аппе: распознавание речи в браузере
(Web Speech API) в WebView Telegram недоступно или капризно, а запись
голосового — родная функция клиента, она работает всегда.
"""
from __future__ import annotations

import json
import secrets

from .idempotency import get_redis

TTL = 10 * 60

WAITING = "waiting"   # ссылка выдана, юзер ещё не открыл чат
ARMED = "armed"       # бот ждёт голосовое
DONE = "done"
ERROR = "error"


def _skey(token: str) -> str:
    return f"voicecap:token:{token}"


def _ukey(telegram_id: int) -> str:
    return f"voicecap:user:{telegram_id}"


async def create(telegram_id: int) -> str:
    token = secrets.token_urlsafe(16)
    payload = {
        "status": WAITING,
        "telegram_id": telegram_id,
        "fields": None,
        "transcript": None,
        "error": None,
    }
    r = get_redis()
    await r.set(_skey(token), json.dumps(payload), ex=TTL)
    await r.set(_ukey(telegram_id), token, ex=TTL)
    return token


async def get(token: str) -> dict | None:
    raw = await get_redis().get(_skey(token))
    return json.loads(raw) if raw else None


async def get_active_for_user(telegram_id: int) -> tuple[str, dict] | None:
    r = get_redis()
    token = await r.get(_ukey(telegram_id))
    if not token:
        return None
    data = await get(token)
    return (token, data) if data else None


async def _save(token: str, data: dict) -> None:
    r = get_redis()
    ttl = await r.ttl(_skey(token))
    await r.set(_skey(token), json.dumps(data), ex=ttl if ttl and ttl > 0 else TTL)


async def arm(token: str) -> dict | None:
    data = await get(token)
    if data is None or data["status"] in (DONE, ERROR):
        return None
    data["status"] = ARMED
    await _save(token, data)
    return data


async def finish(token: str, fields: dict, transcript: str) -> None:
    data = await get(token)
    if data is None:
        return
    data.update(status=DONE, fields=fields, transcript=transcript)
    await _save(token, data)
    await get_redis().delete(_ukey(data["telegram_id"]))


async def fail(token: str, message: str) -> None:
    data = await get(token)
    if data is None:
        return
    data.update(status=ERROR, error=message)
    await _save(token, data)
    await get_redis().delete(_ukey(data["telegram_id"]))


async def cancel(token: str) -> None:
    data = await get(token)
    if data is not None:
        await get_redis().delete(_ukey(data["telegram_id"]))
    await get_redis().delete(_skey(token))
