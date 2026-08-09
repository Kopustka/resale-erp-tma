"""Шаблоны подписей для постов в канал: плейсхолдеры, рендер, валидация.

Тело шаблона — обычный текст с HTML-разметкой Telegram и плейсхолдерами
вида {title}. Экранируются ТОЛЬКО подставляемые значения: сама разметка
шаблона должна дойти до Telegram живой.

Правило пустой строки: если в строке есть плейсхолдеры и все они пустые —
строка выбрасывается целиком. Так «📐 {measurements}» исчезает у вещи без
замеров, не оставляя висящей иконки.
"""
from __future__ import annotations

import re

MAX_CAPTION = 1024

# Разметка, которую понимает Telegram. Всё остальное в теле шаблона запрещаем
# на сохранении — иначе каждый пост будет падать с 400 на стороне Bot API.
ALLOWED_TAGS = {
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "a", "code", "pre", "blockquote", "tg-spoiler", "span", "br",
}
VOID_TAGS = {"br"}

# Всё в фигурных скобках считается попыткой поставить плейсхолдер — включая
# кириллицу и другой регистр. Иначе выдумка модели вроде {размер} прошла бы
# валидацию и утекла литеральным текстом в каждый пост.
_PLACEHOLDER_RE = re.compile(r"\{([^{}\n]{1,40})\}")
_TAG_RE = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>")


class TemplateError(Exception):
    """Шаблон не проходит валидацию."""


# --------------------------------------------------------------------------- #
# Реестр плейсхолдеров (он же источник палитры в мини-аппе)
# --------------------------------------------------------------------------- #
PLACEHOLDERS: list[dict[str, str]] = [
    {"key": "title", "label": "Название", "example": "Archive Nike Zip-up"},
    {"key": "description", "label": "Описание", "example": "Плотная кофта на молнии с высоким воротом. Хорошо садится оверсайз."},
    {"key": "brand", "label": "Бренд", "example": "Nike"},
    {"key": "category", "label": "Категория", "example": "Зип-худи"},
    {"key": "size", "label": "Размер", "example": "L"},
    {"key": "color", "label": "Цвет", "example": "Чёрный"},
    {"key": "condition", "label": "Состояние", "example": "8/10"},
    {"key": "sku", "label": "Артикул", "example": "#1042"},
    {"key": "price", "label": "Цена с валютой", "example": "150 Br"},
    {"key": "price_num", "label": "Цена числом", "example": "150"},
    {"key": "currency", "label": "Символ валюты", "example": "Br"},
    {"key": "price_line", "label": "Строка цены (с «в личку», если цены нет)", "example": "💰 150 Br"},
    {"key": "length", "label": "Длина, см", "example": "72"},
    {"key": "width", "label": "Ширина, см", "example": "58"},
    {"key": "sleeve", "label": "Рукав, см", "example": "65"},
    {"key": "measurements", "label": "Замеры одной строкой", "example": "Длина 72 · Ширина 58 · Рукав 65"},
    {"key": "hashtags", "label": "Хэштеги", "example": "#Nike #Зипхуди #L"},
    {"key": "signature", "label": "Подпись канала", "example": "Написать: @seller"},
]

VALID_KEYS = {p["key"] for p in PLACEHOLDERS}

# Воспроизводит подпись, которая раньше была захардкожена в build_caption(),
# — чтобы у складов без своего шаблона поведение не изменилось.
DEFAULT_TEMPLATE_BODY = (
    "<b>{title}</b>\n"
    "\n"
    "{description}\n"
    "\n"
    "{price_line}\n"
    "📐 {measurements}\n"
    "\n"
    "{signature}"
)

DEMO_ITEM: dict = {
    "title": "Archive Nike Zip-up",
    "description": "Плотная кофта на молнии с высоким воротом. Хорошо садится оверсайз.",
    "brand": "Nike",
    "category": "Зип-худи",
    "size": "L",
    "color": "Чёрный",
    "condition": "8/10",
    "sku": "#1042",
    "price": 150,
    "price_currency": "BYN",
    "length_cm": 72,
    "width_cm": 58,
    "sleeve_cm": 65,
}
DEMO_SIGNATURE = "Написать: @seller"


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_num(v) -> str:
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return str(int(f)) if f == int(f) else str(f)


def _hashtag(value: str) -> str:
    """«Stone Island» -> #StoneIsland. Пустая строка, если тег вырождается."""
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]+", "", value or "")
    return f"#{cleaned}" if cleaned else ""


def build_context(item: dict, signature: str | None = None) -> dict[str, str]:
    """Готовит значения плейсхолдеров. Всё уже HTML-экранировано."""
    from .fx import symbol as cur_symbol

    cur = cur_symbol(item.get("price_currency") or "BYN")
    price_num = _fmt_num(item.get("price"))
    price = f"{price_num} {cur}" if price_num else ""
    price_line = f"💰 {price}" if price_num else "💬 Цена — в личные сообщения"

    meas_parts = []
    for label, key in (("Длина", "length_cm"), ("Ширина", "width_cm"), ("Рукав", "sleeve_cm")):
        val = _fmt_num(item.get(key))
        if val:
            meas_parts.append(f"{label} {val}")

    tags = [_hashtag(str(item.get(k) or "")) for k in ("brand", "category", "size")]

    raw: dict[str, str] = {
        "title": str(item.get("title") or "").strip(),
        "description": str(item.get("description") or "").strip(),
        "brand": str(item.get("brand") or "").strip(),
        "category": str(item.get("category") or "").strip(),
        "size": str(item.get("size") or "").strip(),
        "color": str(item.get("color") or "").strip(),
        "condition": str(item.get("condition") or "").strip(),
        "sku": str(item.get("sku") or "").strip(),
        "price": price,
        "price_num": price_num,
        "currency": cur if price_num else "",
        "price_line": price_line,
        "length": _fmt_num(item.get("length_cm")),
        "width": _fmt_num(item.get("width_cm")),
        "sleeve": _fmt_num(item.get("sleeve_cm")),
        "measurements": " · ".join(meas_parts),
        "hashtags": " ".join(t for t in tags if t),
        "signature": str(signature or "").strip(),
    }
    # price_line непустой всегда — экранируем уже готовые значения.
    return {k: esc(v) for k, v in raw.items()}


def render(body: str, context: dict[str, str]) -> str:
    """Подставляет значения, выбрасывает опустевшие строки, режет до лимита."""
    out_lines: list[str] = []
    for line in body.split("\n"):
        keys = _PLACEHOLDER_RE.findall(line)
        # Незнакомый ключ трактуем как пустое значение — в пост он не попадёт.
        if keys and all(not context.get(k, "") for k in keys):
            continue  # все плейсхолдеры строки пусты — строки быть не должно
        out_lines.append(_PLACEHOLDER_RE.sub(lambda m: context.get(m.group(1), ""), line))

    # Схлопываем пустоты, оставшиеся после выброшенных строк.
    collapsed: list[str] = []
    for line in out_lines:
        if not line.strip():
            if collapsed and not collapsed[-1].strip():
                continue
            if not collapsed:
                continue
        collapsed.append(line.rstrip())
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()

    return "\n".join(collapsed)[:MAX_CAPTION]


def render_demo(body: str) -> str:
    """Превью шаблона на демо-вещи — для редактора в мини-аппе."""
    return render(body, build_context(DEMO_ITEM, DEMO_SIGNATURE))


def unknown_placeholders(body: str) -> list[str]:
    return sorted({k for k in _PLACEHOLDER_RE.findall(body) if k not in VALID_KEYS})


def validate_markup(body: str) -> None:
    """Проверяет теги: только разрешённые и корректно вложенные.

    Смысл в том, чтобы поймать битую разметку один раз на сохранении, а не
    ловить 400 от Bot API на каждом посте.
    """
    stack: list[str] = []
    for m in _TAG_RE.finditer(body):
        closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if tag not in ALLOWED_TAGS:
            raise TemplateError(f"Тег <{tag}> Telegram не поддерживает")
        if tag in VOID_TAGS or attrs.rstrip().endswith("/"):
            continue
        if closing:
            if not stack or stack[-1] != tag:
                raise TemplateError(f"Закрывающий тег </{tag}> без пары")
            stack.pop()
        else:
            stack.append(tag)
    if stack:
        raise TemplateError(f"Тег <{stack[-1]}> не закрыт")


def validate_body(body: str) -> None:
    """Полная проверка тела шаблона перед сохранением."""
    if not body or not body.strip():
        raise TemplateError("Шаблон пустой")
    unknown = unknown_placeholders(body)
    if unknown:
        known = ", ".join(sorted(VALID_KEYS))
        raise TemplateError(
            "Неизвестные плейсхолдеры: "
            + ", ".join("{" + u + "}" for u in unknown)
            + f". Доступны: {known}"
        )
    validate_markup(body)
    if not render_demo(body).strip():
        raise TemplateError("На демо-данных шаблон даёт пустой пост")
