"""Автоответы на типовые вопросы в комментариях под постом.

Отвечаем только на то, что уверенно распознали и на что есть данные в
карточке. Молчание лучше отписки невпопад: под постом это видят все.

Регулярки, а не нейросеть: вопросы однотипные («какие замеры?», «сколько
стоит?»), а вызов модели на каждый комментарий — это и задержка, и деньги,
и риск выдумки.
"""
from __future__ import annotations

import re
from decimal import Decimal

# Порядок важен: более конкретные темы проверяются раньше общих.
TOPICS: list[tuple[str, re.Pattern[str]]] = [
    ("measurements", re.compile(
        r"замер|обмер|длин|ширин|рукав|плеч|пог|ог\b|размеры\s+вещи|сколько\s+см|"
        r"по\s+длине|по\s+ширине", re.I)),
    ("size", re.compile(r"\bразмер\b|какой\s+рост|сяд[еи]т|подойд[её]т|\bs\b|\bm\b|\bl\b", re.I)),
    ("price", re.compile(r"цена|сколько\s+стоит|поч[её]м|стоимость|за\s+сколько|торг", re.I)),
    ("condition", re.compile(r"состояни|дефект|дыр|пятн|потёрт|потерт|износ|качеств|новая\s+ли", re.I)),
    ("available", re.compile(r"актуальн|в\s+наличии|ещ[её]\s+есть|продан|свободн|заберу|беру", re.I)),
]

SOLD_STATUSES_STR = {"SOLD", "SHIPPED", "COMPLETED"}


def detect_topics(text: str) -> list[str]:
    """Какие темы затронуты в комментарии. Пустой список — не отвечаем."""
    t = (text or "").strip()
    if not t or len(t) > 300:
        return []
    return [name for name, rx in TOPICS if rx.search(t)]


def _fmt(v) -> str:
    if v is None:
        return ""
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)


def build_reply(item: dict, topics: list[str]) -> str | None:
    """Собирает ответ по темам. None — сказать нечего, лучше промолчать."""
    parts: list[str] = []
    status = str(item.get("status") or "")
    sold = status in SOLD_STATUSES_STR

    for topic in topics:
        if topic == "measurements":
            meas = []
            for label, key in (("Длина", "length_cm"), ("Ширина", "width_cm"), ("Рукав", "sleeve_cm")):
                val = _fmt(item.get(key))
                if val:
                    meas.append(f"{label} {val} см")
            if meas:
                parts.append("📐 " + " · ".join(meas))
        elif topic == "size":
            if item.get("size"):
                parts.append(f"📌 Размер: {item['size']}")
        elif topic == "price":
            price = item.get("price")
            if price is not None:
                parts.append(f"💰 Цена: {_fmt(price)} {item.get('currency') or ''}".strip())
        elif topic == "condition":
            if item.get("condition"):
                parts.append(f"✨ Состояние: {item['condition']}")
        elif topic == "available":
            parts.append("❌ Уже продано." if sold else "✅ Да, вещь актуальна.")

    if not parts:
        return None
    # Дубли возможны, если вопрос задел две темы с одним ответом.
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return "\n".join(seen)


def answer(item: dict, text: str) -> str | None:
    """Полный путь: распознать вопрос и собрать ответ. None — молчим."""
    topics = detect_topics(text)
    if not topics:
        return None
    return build_reply(item, topics)
