"""Разбор голосовой фразы в поля вещи через Gemini.

Словарный парсер (voice_parser) понимал только заранее известные слова и
жёсткие обороты: «найк», «эль», «восемь из десяти». Живая речь в них не
укладывается — «взял адидасовскую олимпийку, сорок пятый размер, отдал
полтинник, состояние отличное» он разбирал наполовину.

Модель работает со смыслом, а не с ключевыми словами: понимает падежи,
разговорные названия брендов, «за полтос», «почти новая».

Словарный парсер остаётся запасным путём: без ключа, при исчерпанной
квоте или сбое сети функция обязана продолжать работать.
"""
from __future__ import annotations

import asyncio
import json
import logging

import httpx

from ..config import get_settings

settings = get_settings()
log = logging.getLogger("ai_voice")

MAX_TEXT = 600

PROMPT = """Ты разбираешь речь продавца б/у одежды и раскладываешь её по полям карточки товара.

Пользователь надиктовал одну фразу про вещь. Извлеки из неё то, что реально сказано.

Поля:
- brand — бренд. Приводи к общепринятому написанию латиницей: «найк»/«найки»/«나이키» -> Nike, «адидас»/«адик» -> Adidas, «стоник»/«стон айленд» -> Stone Island, «тнф»/«зэ норт фейс» -> The North Face, «кархарт» -> Carhartt, «зара» -> Zara. Незнакомый бренд оставь как услышал, с большой буквы.
- category — вид вещи по-русски, в единственном числе и именительном падеже: «худи», «зип-худи», «футболка», «лонгслив», «рубашка», «джинсы», «карго», «куртка», «олимпийка», «свитер», «кроссовки».
- size — размер как принято: XS, S, M, L, XL, XXL либо число (44, 46, 52). «эль» -> L, «эмка» -> M, «икс эль» -> XL.
- color — цвет по-русски, одним словом в именительном падеже: «чёрный», «синий», «бежевый».
- condition — состояние строго в виде «N/10», где N от 1 до 10. «восемь из десяти» -> 8/10, «идеальное»/«как новая» -> 10/10, «отличное» -> 9/10, «хорошее» -> 8/10, «среднее» -> 6/10, «убитая» -> 4/10.
- cost_price — за сколько вещь КУПЛЕНА (закупка). Только число. «полтос»/«полтинник» -> 50, «стольник» -> 100, «двушка» -> 200, «косарь» -> 1000.
- list_price — за сколько ПРОДАЁТСЯ (цена в объявлении). Только число.
- title — короткое торговое название латиницей, если из фразы понятно, что это за вещь. Формат «Бренд Категория», можно с поисковым словом: «Archive», «Vintage». Максимум 60 символов.

Правила:
1. Чего в речи НЕТ — ставь null. Ничего не додумывай и не угадывай.
2. Различай закупку и продажу. «взял за 50, продаю за 150» -> cost_price=50, list_price=150. Если сумма одна и сказано «купил/взял/отдал/обошлась» — это cost_price. Если «продаю/отдаю/цена/ставлю» — это list_price. Если непонятно, к чему сумма, — cost_price.
3. Числа возвращай числами, без валюты и пробелов.
4. Речь распознана автоматически, в ней бывают ошибки — опирайся на смысл, а не на буквальное написание.
5. low_confidence поставь true, если фраза обрывочная, много не разобрано или ты не уверен в прочтении.

Ответь СТРОГО в формате JSON без markdown и пояснений:
{"brand": null, "category": null, "size": null, "color": null, "condition": null, "cost_price": null, "list_price": null, "title": null, "low_confidence": false}"""


class VoiceAiUnavailable(Exception):
    """Модель недоступна — вызывающий должен откатиться на словарный разбор."""


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    # Отрицательные и абсурдные суммы — вернее ошибка распознавания.
    return n if 0 < n < 10_000_000 else None


def _text(v, limit: int) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip()
    return s[:limit] if s else None


def _clean(parsed: dict) -> dict:
    """Приводит ответ модели к контракту. Ответу не доверяем."""
    condition = _text(parsed.get("condition"), 8)
    if condition and "/" not in condition:
        condition = None  # модель нарушила формат — лучше пусто, чем мусор
    return {
        "brand": _text(parsed.get("brand"), 80),
        "category": _text(parsed.get("category"), 60),
        "size": _text(parsed.get("size"), 20),
        "color": _text(parsed.get("color"), 40),
        "condition": condition,
        "cost_price": _num(parsed.get("cost_price")),
        "list_price": _num(parsed.get("list_price")),
        "title": _text(parsed.get("title"), 100),
        "low_confidence": bool(parsed.get("low_confidence")),
    }


async def parse_voice_ai(text: str) -> dict:
    """Разбирает фразу моделью. Бросает VoiceAiUnavailable — тогда откат."""
    if not settings.gemini_api_key:
        raise VoiceAiUnavailable("нет ключа")
    phrase = (text or "").strip()
    if not phrase:
        raise VoiceAiUnavailable("пустая фраза")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    body = {
        "contents": [{"parts": [{"text": PROMPT + "\n\nФРАЗА:\n" + phrase[:MAX_TEXT]}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            # Извлечение фактов, а не сочинение: низкая температура.
            "temperature": 0.1,
        },
    }
    # 503 у Gemini — частая кратковременная перегрузка. Разбор голоса
    # пользователь ждёт, поэтому лишние полсекунды дешевле, чем молчаливый
    # откат на словарь, который читает «сорок третий» как размер 40.
    r = None
    attempts = 3
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    url, json=body, headers={"x-goog-api-key": settings.gemini_api_key}
                )
        except Exception as e:  # noqa: BLE001
            if attempt < attempts - 1:
                await asyncio.sleep(0.6 * (attempt + 1))
                continue
            raise VoiceAiUnavailable(f"сеть: {e}")
        if r.status_code in (500, 502, 503) and attempt < attempts - 1:
            await asyncio.sleep(0.6 * (attempt + 1))
            continue
        break

    if r is None or r.status_code != 200:
        raise VoiceAiUnavailable(f"HTTP {r.status_code if r else '—'}")
    try:
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(raw)
    except (KeyError, IndexError, json.JSONDecodeError, TypeError) as e:
        raise VoiceAiUnavailable(f"разбор ответа: {e}")
    if not isinstance(parsed, dict):
        raise VoiceAiUnavailable("ответ не объект")
    return _clean(parsed)
