"""Единая точка обращения к Gemini: повторы, запасная модель, разбор JSON.

Логика вызова была продублирована в трёх местах (описание по фото, шаблоны,
голос), и чинить приходилось бы в трёх. Здесь она одна.

Два режима отказа лечатся по-разному:
- 500/502/503 — кратковременная перегрузка, помогает повтор;
- 429 — квота исчерпана, повторять бессмысленно. Переходим на запасную
  модель: у неё отдельный лимит, и лучше ответить чуть проще, чем не
  ответить вовсе.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from ..config import get_settings

settings = get_settings()
log = logging.getLogger("gemini")

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TRANSIENT = (500, 502, 503)
QUOTA = 429


class GeminiUnavailable(Exception):
    """Модель недоступна. Вызывающий решает, чем это заменить."""


class GeminiQuotaExceeded(GeminiUnavailable):
    """Исчерпаны лимиты и основной, и запасной модели."""


def _models() -> list[str]:
    primary = settings.gemini_model
    fallback = getattr(settings, "gemini_fallback_model", "") or ""
    out = [primary]
    if fallback and fallback != primary:
        out.append(fallback)
    return out


async def _post(model: str, body: dict, timeout: int) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(
            ENDPOINT.format(model=model),
            json=body,
            headers={"x-goog-api-key": settings.gemini_api_key},
        )


async def call_json(
    parts: list[dict],
    *,
    timeout: int = 45,
    temperature: float = 0.2,
    attempts: int = 3,
) -> dict:
    """Запрос со строгим JSON на выходе. Бросает GeminiUnavailable."""
    if not settings.gemini_api_key:
        raise GeminiUnavailable("нет ключа")

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": temperature,
        },
    }

    last = "неизвестно"
    quota_hit = False
    for model in _models():
        for attempt in range(attempts):
            try:
                r = await _post(model, body, timeout)
            except Exception as e:  # noqa: BLE001
                last = f"сеть: {e}"
                if attempt < attempts - 1:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                break

            if r.status_code == 200:
                try:
                    raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = json.loads(raw)
                except (KeyError, IndexError, json.JSONDecodeError, TypeError) as e:
                    raise GeminiUnavailable(f"разбор ответа: {e}")
                if not isinstance(parsed, dict):
                    raise GeminiUnavailable("ответ не объект")
                return parsed

            if r.status_code == QUOTA:
                # Повторять бессмысленно — сразу к запасной модели.
                quota_hit = True
                last = "квота исчерпана"
                log.warning("квота исчерпана у %s, пробуем запасную", model)
                break

            if r.status_code in TRANSIENT and attempt < attempts - 1:
                last = f"HTTP {r.status_code}"
                await asyncio.sleep(0.8 * (attempt + 1))
                continue

            last = f"HTTP {r.status_code}"
            break

    if quota_hit:
        raise GeminiQuotaExceeded("дневная квота Gemini исчерпана")
    raise GeminiUnavailable(last)
