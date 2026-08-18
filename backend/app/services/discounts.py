"""Скидки: расчёт, округление, объявление в канале.

Объявление уходит ОТВЕТОМ на пост вещи, а не отдельным сообщением: так
подписчик видит, к чему относится новая цена, и может сразу открыть
исходную карточку с фото.
"""
from __future__ import annotations

import logging
from decimal import Decimal, ROUND_FLOOR

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Channel, Discount, Item, ItemPost, JobKind, JobStatus, PostJob
from . import post_queue

log = logging.getLogger("discounts")

# Быстрые кнопки в интерфейсе. Держим здесь, чтобы фронт и бэкенд
# считали одинаково и процент в объявлении сходился с нажатой кнопкой.
QUICK_PERCENTS = (10, 20, 30)

# Шаг округления зависит от валюты. В рублях РФ ценник с единицами
# выглядит неопрятно («1547»), поэтому округляем до десятков. В белорусских
# суммы на порядок меньше, там достаточно убрать копейки.
ROUND_STEPS: dict[str, Decimal] = {
    "RUB": Decimal("10"),
    "BYN": Decimal("1"),
    "USD": Decimal("1"),
    "EUR": Decimal("1"),
}
DEFAULT_STEP = Decimal("1")


def step_for(currency: str | None) -> Decimal:
    return ROUND_STEPS.get((currency or "").upper(), DEFAULT_STEP)


def round_price(value: Decimal, currency: str | None = None) -> Decimal:
    """Округление ВНИЗ до шага валюты.

    Вниз, а не к ближайшему: при округлении вверх покупатель получил бы
    скидку меньше обещанной, и «−30%» оказалось бы неправдой.
    """
    step = step_for(currency)
    raw = Decimal(value)
    stepped = (raw / step).to_integral_value(rounding=ROUND_FLOOR) * step
    # Дешёвая вещь при крупном шаге дала бы ноль и упёрлась в проверку
    # «цена больше нуля». Для такого вырожденного случая режем до целых.
    if stepped <= 0 < raw:
        return raw.to_integral_value(rounding=ROUND_FLOOR)
    return stepped


def apply_percent(price: Decimal, percent: int, currency: str | None = None) -> Decimal:
    """Цена со скидкой в N процентов, округлённая вниз по шагу валюты."""
    raw = Decimal(price) * (Decimal(100 - percent) / Decimal(100))
    return round_price(raw, currency)


def percent_of(old: Decimal, new: Decimal) -> int:
    """Фактический процент скидки — считаем по ценам, а не по кнопке."""
    if not old or Decimal(old) <= 0:
        return 0
    return int(round((1 - Decimal(new) / Decimal(old)) * 100))


def _fmt(value: Decimal) -> str:
    f = float(value)
    return str(int(f)) if f == int(f) else f"{f:.2f}".rstrip("0").rstrip(".")


# Плейсхолдеры шаблона объявления. Свой набор, а не общий с постами:
# у скидки речь про две цены и процент, остальных полей вещи тут нет.
PLACEHOLDERS: list[dict[str, str]] = [
    {"key": "title", "label": "Название вещи", "example": "Archive Nike Zip-up"},
    {"key": "sku", "label": "Артикул", "example": "#1042"},
    {"key": "old_price", "label": "Старая цена", "example": "150"},
    {"key": "new_price", "label": "Новая цена", "example": "105"},
    {"key": "percent", "label": "Процент скидки", "example": "30"},
    {"key": "currency", "label": "Символ валюты", "example": "Br"},
    {"key": "size", "label": "Размер", "example": "L"},
    {"key": "signature", "label": "Подпись канала", "example": "Написать: @seller"},
]
VALID_KEYS = {p["key"] for p in PLACEHOLDERS}

DEFAULT_TEMPLATE = (
    "🔥 <b>СКИДКА −{percent}%</b>\n"
    "{title}\n"
    "<s>{old_price} {currency}</s> → <b>{new_price} {currency}</b>"
)


def build_context(discount: Discount, item: Item | None, symbol: str) -> dict[str, str]:
    """Значения плейсхолдеров. Всё уже HTML-экранировано."""
    from .post_template import esc

    pct = percent_of(discount.old_price, discount.new_price)
    raw = {
        "title": (item.title if item else "") or "",
        "sku": (item.sku if item else "") or "",
        "old_price": _fmt(discount.old_price),
        "new_price": _fmt(discount.new_price),
        # Пустой процент выбросит строку целиком — то же правило, что
        # и у шаблонов постов.
        "percent": str(pct) if pct > 0 else "",
        "currency": symbol,
        "size": (item.size if item else "") or "",
        "signature": "",
    }
    return {k: esc(str(v).strip()) for k, v in raw.items()}


def validate_template(body: str) -> None:
    """Проверяет шаблон перед сохранением. Бросает TemplateError."""
    from .post_template import _PLACEHOLDER_RE, TemplateError, validate_markup

    if not body or not body.strip():
        raise TemplateError("Шаблон пустой")
    unknown = sorted({k for k in _PLACEHOLDER_RE.findall(body) if k not in VALID_KEYS})
    if unknown:
        raise TemplateError(
            "Неизвестные плейсхолдеры: "
            + ", ".join("{" + u + "}" for u in unknown)
            + ". Доступны: " + ", ".join(sorted(VALID_KEYS))
        )
    validate_markup(body)
    if not render_demo(body).strip():
        raise TemplateError("На примере шаблон даёт пустой текст")


def render_demo(body: str) -> str:
    """Превью шаблона на примере — для настроек."""
    from .post_template import render

    return render(body, {p["key"]: p["example"] for p in PLACEHOLDERS})


def build_message(
    discount: Discount,
    item: Item | None,
    symbol: str,
    template: str | None = None,
    signature: str | None = None,
) -> str:
    """Текст объявления. Уходит ответом на пост вещи."""
    from .post_template import esc, render

    ctx = build_context(discount, item, symbol)
    if signature:
        ctx["signature"] = esc(signature.strip())
    return render(template or DEFAULT_TEMPLATE, ctx)


async def enqueue_announcements(
    session: AsyncSession, discount: Discount, run_at=None
) -> int:
    """Ставит объявление в каждый канал, где вещь опубликована и не продана."""
    rows = (
        await session.execute(
            select(ItemPost, Channel)
            .join(Channel, Channel.id == ItemPost.channel_id)
            .where(
                ItemPost.item_id == discount.item_id,
                ItemPost.sold_marked.is_(False),
                Channel.enabled.is_(True),
            )
        )
    ).all()
    if not rows:
        return 0

    queued = set(
        (
            await session.execute(
                select(PostJob.channel_uid).where(
                    PostJob.discount_id == discount.id,
                    PostJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                )
            )
        ).scalars().all()
    )

    n = 0
    for post, ch in rows:
        if ch.id in queued:
            continue
        await post_queue.enqueue(
            session,
            store_id=discount.store_id,
            kind=JobKind.DISCOUNT_POST,
            channel_id=ch.chat_id,
            channel_uid=ch.id,
            item_id=discount.item_id,
            discount_id=discount.id,
            # Ответ вешаем на пост вещи в этом канале.
            message_id=post.message_id,
            run_at=run_at,
        )
        n += 1
    if n:
        await session.commit()
    return n
