"""Форма вещи: набор по умолчанию и правила его правки.

Магазины ведут разный товар. Одному нужны замеры рукава, другому — номер
лота и площадка, третьему хватает названия и цены. Держать один жёсткий
набор полей значит заставлять половину клиентов пролистывать чужое.

Поэтому форма настраивается, но не с нуля: при первом обращении склад
получает набор по умолчанию — тот, с которым приложение работало раньше.
Дальше магазин прячет лишнее, переименовывает под свой язык и добавляет
своё.

Встроенные поля удалить нельзя, только скрыть: на них завязаны аналитика,
поиск и подсказки, и удаление оставило бы дыры в уже заведённых вещах.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import FieldKind, StoreField

#: Ключ, подпись, тип, обязательность, включено ли по умолчанию.
#: Порядок здесь — порядок в форме. Совпадает с тем, что было до настройки,
#: чтобы у существующих складов ничего не переехало.
DEFAULTS: list[tuple[str, str, FieldKind, bool, bool]] = [
    ("title", "Название", FieldKind.TEXT, False, True),
    ("brand", "Бренд", FieldKind.TEXT, True, True),
    ("category", "Категория", FieldKind.TEXT, True, True),
    ("size", "Размер", FieldKind.TEXT, False, True),
    ("color", "Цвет", FieldKind.TEXT, False, True),
    ("condition", "Состояние", FieldKind.TEXT, False, True),
    ("description", "Описание", FieldKind.TEXTAREA, False, True),
    ("length_cm", "Длина, см", FieldKind.NUMBER, False, True),
    ("width_cm", "Ширина, см", FieldKind.NUMBER, False, True),
    ("sleeve_cm", "Рукав, см", FieldKind.NUMBER, False, True),
    ("cost_price", "Закупка", FieldKind.MONEY, False, True),
    ("restore_cost", "Реставрация", FieldKind.MONEY, False, True),
    ("delivery_cost", "Доставка", FieldKind.MONEY, False, True),
    ("list_price", "Цена продажи", FieldKind.MONEY, False, True),
    ("purchase_location", "Где куплено", FieldKind.TEXT, False, False),
    ("sales_platform", "Площадка", FieldKind.TEXT, False, False),
]

#: Ключи встроенных полей — их нельзя удалить и нельзя завести заново.
BUILTIN_KEYS = {k for k, *_ in DEFAULTS}

#: Поля, без которых вещь не создать: их не скрыть и не сделать необязательными.
LOCKED_KEYS = {"brand", "category"}

_SLUG = re.compile(r"[^a-z0-9_]+")


def make_key(label: str, taken: set[str]) -> str:
    """Ключ для своего поля из его названия.

    Ключом пользуется JSON в items.extra, поэтому он должен быть
    предсказуемым и не совпадать со встроенными: иначе своё поле
    затирало бы колонку вещи.
    """
    translit = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        "abvgdeejzijklmnoprstufhccss_y_eua",
    )
    base = _SLUG.sub("_", label.strip().lower().translate(translit)).strip("_")
    base = base[:30] or "field"
    if base in BUILTIN_KEYS:
        base = f"{base}_custom"
    key = base
    n = 2
    while key in taken:
        key = f"{base}_{n}"
        n += 1
    return key


async def ensure_defaults(session: AsyncSession, store_id: uuid.UUID) -> list[StoreField]:
    """Набор полей склада. При первом обращении создаёт стандартный.

    Досоздаёт и по одному: если в новой версии приложения появилось
    встроенное поле, старые склады получат его молча, а не останутся с
    формой, в которой его нет.
    """
    rows = list(
        (
            await session.execute(
                select(StoreField)
                .where(StoreField.store_id == store_id)
                .order_by(StoreField.position, StoreField.created_at)
            )
        ).scalars()
    )
    have = {f.key for f in rows}
    added = False
    for pos, (key, label, kind, required, enabled) in enumerate(DEFAULTS):
        if key in have:
            continue
        session.add(
            StoreField(
                store_id=store_id,
                key=key,
                label=label,
                kind=kind,
                required=required,
                enabled=enabled,
                position=pos,
                builtin=True,
            )
        )
        added = True
    if added:
        await session.commit()
        rows = list(
            (
                await session.execute(
                    select(StoreField)
                    .where(StoreField.store_id == store_id)
                    .order_by(StoreField.position, StoreField.created_at)
                )
            ).scalars()
        )
    return rows


def split_payload(data: dict, fields: list[StoreField]) -> tuple[dict, dict]:
    """Делит присланные значения на колонки вещи и свои поля.

    Ключи, которых нет ни среди колонок, ни среди объявленных полей, тоже
    убираем из data: дальше по ним пошёл бы Item(**data) и упал бы с
    непонятной ошибкой про неизвестный аргумент.
    """
    custom_keys = {f.key for f in fields if not f.builtin}
    extra: dict = {}
    for k in list(data.keys()):
        if k in custom_keys:
            extra[k] = data.pop(k)
        elif k not in BUILTIN_KEYS and k not in _COLUMN_EXTRAS:
            data.pop(k)
    return data, extra


#: Колонки Item, которые приходят в теле запроса, но полем формы не являются.
_COLUMN_EXTRAS = {
    "photo_file_ids", "ad_url", "cost_currency", "price_currency",
    "platform_fee", "selling_price",
}


def filter_extra(extra: dict, fields: list[StoreField]) -> dict:
    """Оставляет только значения объявленных своих полей.

    Клиент может прислать что угодно: без этой отсечки в items.extra
    накапливались бы ключи, которых нет в форме, и никто бы их не увидел
    и не почистил.
    """
    allowed = {f.key for f in fields if not f.builtin}
    return {k: v for k, v in (extra or {}).items() if k in allowed}


def missing_required(
    fields: list[StoreField], columns: dict, extra: dict
) -> list[str]:
    """Названия обязательных полей, которые остались пустыми."""
    out = []
    for f in fields:
        if not f.enabled or not f.required:
            continue
        value = extra.get(f.key) if not f.builtin else columns.get(f.key)
        if value is None or (isinstance(value, str) and not value.strip()):
            out.append(f.label)
    return out
