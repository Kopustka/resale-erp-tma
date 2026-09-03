"""Разбор голосовой фразы в поля новой вещи (create по голосу).

Пример: "найк худи размер эль чёрный состояние восемь из десяти закупка сорок"
    -> brand=nike, category=худи, size=L, color=чёрный, condition=8/10, cost_price=40

Regex/lightweight NLP: словари брендов/категорий/цветов, буквенные и числовые
размеры, числительные словами, ключевые слова для состояния и цены.
Что не распозналось — остаётся None (пользователь дозаполнит руками).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# --- Бренды: алиас -> каноническое имя --------------------------------------
BRAND_ALIASES: dict[str, str] = {
    "найк": "Nike", "найки": "Nike", "nike": "Nike", "найка": "Nike",
    "адидас": "Adidas", "adidas": "Adidas", "адик": "Adidas",
    "пума": "Puma", "puma": "Puma",
    "стон": "Stone Island", "стоник": "Stone Island", "stone": "Stone Island",
    "кархарт": "Carhartt", "carhartt": "Carhartt",
    "норт": "The North Face", "тнф": "The North Face", "tnf": "The North Face",
    "ральф": "Ralph Lauren", "поло": "Ralph Lauren",
    "томми": "Tommy Hilfiger", "tommy": "Tommy Hilfiger",
    "прада": "Prada", "prada": "Prada",
    "гуччи": "Gucci", "gucci": "Gucci",
    "коламбия": "Columbia", "columbia": "Columbia",
    "рибок": "Reebok", "reebok": "Reebok",
    "нью": "New Balance", "нб": "New Balance",
}

# --- Категории: алиас -> каноническое имя -----------------------------------
CATEGORY_ALIASES: dict[str, str] = {
    "худи": "худи", "толстовка": "худи", "кофта": "худи", "hoodie": "худи",
    "куртка": "куртки", "куртки": "куртки", "ветровка": "куртки", "пуховик": "куртки",
    "пальто": "пальто", "плащ": "пальто",
    "шорты": "шорты", "штаны": "штаны", "джинсы": "джинсы", "брюки": "штаны",
    "футболка": "футболки", "майка": "футболки", "тишка": "футболки",
    "свитшот": "свитшоты", "свитер": "свитеры", "лонгслив": "лонгсливы",
    "рубашка": "рубашки", "поло": "поло",
    "кроссы": "обувь", "кроссовки": "обувь", "кеды": "обувь", "обувь": "обувь",
    "сумка": "аксессуары", "рюкзак": "аксессуары", "кепка": "аксессуары",
}

# --- Цвета: стем -> каноническое имя (длинные стемы раньше) ------------------
COLOR_STEMS: list[tuple[str, str]] = [
    ("серебр", "серебряный"),
    ("черн", "чёрный"),
    ("бел", "белый"),
    ("сер", "серый"),
    ("голуб", "голубой"),
    ("син", "синий"),
    ("красн", "красный"),
    ("зелен", "зелёный"),
    ("желт", "жёлтый"),
    ("беж", "бежевый"),
    ("коричн", "коричневый"),
    ("розов", "розовый"),
    ("фиолет", "фиолетовый"),
    ("оранж", "оранжевый"),
    ("бордов", "бордовый"),
    ("золот", "золотой"),
    ("хаки", "хаки"),
    ("разноцвет", "разноцветный"),
]

# --- Буквенные размеры ------------------------------------------------------
LETTER_SIZES: dict[str, str] = {
    "xs": "XS", "s": "S", "m": "M", "l": "L", "xl": "XL", "xxl": "XXL",
    "эс": "S", "эм": "M", "эль": "L", "хл": "XL", "ххл": "XXL", "иксэс": "XS",
}

# --- Числительные словами ---------------------------------------------------
_UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19,
}
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}
_HUNDREDS = {
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
}
_THOUSANDS = {"тысяча": 1000, "тысячи": 1000, "тысяч": 1000}
_SLANG = {"полтос": 50, "полтинник": 50, "стольник": 100, "четвертак": 25, "десятка": 10}

_NUMBER_WORDS = set(_UNITS) | set(_TENS) | set(_HUNDREDS) | set(_THOUSANDS) | set(_SLANG)

# Ключевые слова
_SIZE_CUE = ("размер", "размера", "сайз")
_COND_CUE = ("состояни", "состоян")
_COST_CUE = ("закуп", "себестоимост", "купил", "купила", "отдал", "отдала", "цена", "ценой", "стоит", "взял", "взяла")
_SKIP = {"рублей", "рубль", "рубля", "р", "byn", "бр", "из", "на", "за", "и", "в"}


@dataclass
class ItemParse:
    brand: str | None = None
    category: str | None = None
    size: str | None = None
    color: str | None = None
    condition: str | None = None
    cost_price: float | None = None
    title: str | None = None
    low_confidence: bool = False


def _match_color(tok: str) -> str | None:
    for stem, name in COLOR_STEMS:
        if tok.startswith(stem):
            return name
    return None


def _read_number(tokens: list[str], i: int) -> tuple[int | None, int]:
    """Читает число (цифрами или словами) начиная с tokens[i]. -> (значение, next_i)."""
    tok = tokens[i]
    if tok.isdigit():
        return int(tok), i + 1
    if tok in _SLANG:
        return _SLANG[tok], i + 1
    if tok not in _NUMBER_WORDS:
        return None, i
    total = 0
    j = i
    while j < len(tokens) and tokens[j] in _NUMBER_WORDS:
        w = tokens[j]
        if w in _THOUSANDS:
            total = (total or 1) * 1000
        elif w in _HUNDREDS:
            total += _HUNDREDS[w]
        elif w in _TENS:
            total += _TENS[w]
        elif w in _UNITS:
            total += _UNITS[w]
        elif w in _SLANG:
            total += _SLANG[w]
        j += 1
    return total, j


def parse_item_voice(text: str) -> ItemParse:
    raw = text.lower().replace("ё", "е")
    tokens = re.findall(r"[а-яa-z0-9]+", raw)
    res = ItemParse()

    pending: str | None = None  # 'size' | 'condition' | 'cost'
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]

        # ключевые слова -> задают, к чему относится следующее число
        if tok.startswith(_SIZE_CUE):
            pending = "size"
            i += 1
            continue
        if tok.startswith(_COND_CUE):
            pending = "condition"
            i += 1
            continue
        if tok.startswith(_COST_CUE):
            pending = "cost"
            i += 1
            continue

        # бренд / категория / цвет / буквенный размер
        if res.brand is None and tok in BRAND_ALIASES:
            res.brand = BRAND_ALIASES[tok]
            i += 1
            continue
        if res.category is None and tok in CATEGORY_ALIASES:
            res.category = CATEGORY_ALIASES[tok]
            i += 1
            continue
        color = _match_color(tok)
        if res.color is None and color:
            res.color = color
            i += 1
            continue
        if res.size is None and pending != "condition" and tok in LETTER_SIZES:
            res.size = LETTER_SIZES[tok]
            i += 1
            continue

        # число
        num, ni = _read_number(tokens, i)
        if num is not None:
            # "восемь из десяти" / "восемь на десять" -> состояние
            tail = tokens[ni : ni + 2]
            is_out_of_ten = (
                len(tail) == 2 and tail[0] in {"из", "на"} and tail[1].startswith("десят")
            )
            if pending == "size":
                res.size = str(num)
                pending = None
            elif pending == "condition" or is_out_of_ten:
                res.condition = f"{min(num, 10)}/10"
                pending = None
                if is_out_of_ten:
                    ni += 2
            elif pending == "cost":
                res.cost_price = float(num)
                pending = None
            else:
                # число без явного ключа — эвристика
                if 1 <= num <= 10 and res.condition is None and is_out_of_ten:
                    res.condition = f"{num}/10"
                elif 34 <= num <= 62 and res.size is None:
                    res.size = str(num)
                elif res.cost_price is None and num > 3:
                    res.cost_price = float(num)
            i = ni
            continue

        i += 1

    if res.brand and res.category:
        res.title = f"{res.brand} {res.category}"
    elif res.brand:
        res.title = res.brand

    res.low_confidence = res.brand is None and res.category is None
    return res
