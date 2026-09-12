"""Выгрузка склада в CSV и отправка файлом в чат.

Зачем файл в чат, а не скачивание: мини-апп живёт во вебвью Telegram, и
обычная ссылка на скачивание там ненадёжна — на iOS она часто не делает
ничего вовсе. Документ, присланный ботом, открывается на любом устройстве,
остаётся в переписке и пересылается куда нужно.

Формат подобран под то, чем этими файлами пользуются, — Excel с русской
локалью. Отсюда точка с запятой как разделитель и запятая в числах: с
запятой-разделителем русский Excel сложил бы все колонки в одну, а с
точкой в числах прочитал бы суммы как текст и не дал бы их сложить.
Спецификация RFC 4180 предписывает запятую, но файл, который нельзя
открыть двойным щелчком, пользы не приносит.
"""
from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import FieldKind, Item, ItemStatus, Store, User
from . import fields as fields_svc

settings = get_settings()
log = logging.getLogger("export")

API = f"https://api.telegram.org/bot{settings.bot_token}"

#: Excel в русской локали ждёт точку с запятой; без BOM он же читает
#: UTF-8 как однобайтовую кодировку и показывает кириллицу кракозябрами.
DELIMITER = ";"
BOM = "﻿"

STATUS_LABELS = {
    ItemStatus.BOUGHT: "Куплен",
    ItemStatus.PREPARING: "Подготовка",
    ItemStatus.PHOTOGRAPHED: "Отфотографирован",
    ItemStatus.LISTED: "Выставлен",
    ItemStatus.SHIPPED: "Отправлен",
}

#: Денежные колонки вещи. В форме они тоже есть, но выгружаем их отдельным
#: блоком: рядом с суммой нужна валюта, а считанные поля (прибыль, ROI) в
#: форме не значатся вообще — их нет смысла вводить, только смотреть.
MONEY_COLUMNS = ("cost_price", "restore_cost", "delivery_cost", "list_price")


def _num(value: Decimal | float | int | None) -> str:
    """Число в виде, который русский Excel считает числом."""
    if value is None:
        return ""
    if isinstance(value, Decimal):
        text = format(value.normalize() if value == value.to_integral() else value, "f")
    else:
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _date(value: datetime | None) -> str:
    """Дата без времени: в отчётах по складу часы не нужны."""
    return value.strftime("%d.%m.%Y") if value is not None else ""


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        # Переводы строк внутри ячейки Excel показывает, но в выгрузке из
        # описания они превращают таблицу в лестницу. Схлопываем.
        return " ".join(value.split())
    return str(value)


async def build_items_csv(
    session: AsyncSession,
    store_id: uuid.UUID,
    *,
    show_finance: bool,
    include_archived: bool = False,
) -> tuple[str, bytes, int]:
    """Собирает CSV склада. Возвращает (имя файла, содержимое, число вещей).

    Колонки берутся из настроек формы этого склада: те же названия, тот же
    порядок, скрытые поля не выгружаются. Иначе человек, переименовавший
    «Состояние» в «Оценка 1-10» и убравший замеры, получил бы файл с чужими
    подписями и пустыми столбцами.
    """
    store = (
        await session.execute(select(Store).where(Store.id == store_id))
    ).scalar_one()
    base = (store.base_currency or "BYN").upper()

    form = await fields_svc.ensure_defaults(session, store_id)
    shown = [f for f in form if f.enabled]

    # Денежные поля выгружаются отдельным блоком, из общего списка их убираем.
    columns = [f for f in shown if f.key not in MONEY_COLUMNS]
    if not show_finance:
        # Своё поле типа «деньги» — те же деньги под другим именем. Роль без
        # доступа к финансам не должна получить их и файлом.
        columns = [f for f in columns if f.kind is not FieldKind.MONEY]

    header: list[str] = ["Артикул", "Этап"]
    header += [f.label for f in columns]
    header += ["Дата закупки", "Выставлен", "Отправлен", "Добавлено"]
    if show_finance:
        money_by_key = {f.key: f for f in shown}
        header += [
            money_by_key[k].label if k in money_by_key else k for k in MONEY_COLUMNS
        ]
        # «Цена продажи» — это ценник в объявлении, и подпись у неё своя,
        # из настроек формы. Фактическую сумму сделки называем иначе, иначе
        # в файле оказались бы два столбца с почти одинаковым названием.
        # Валюту прибыли подписываем: суммы стоят в той валюте, в которой их
        # вводили, а прибыль считается в базовой валюте склада. Без подписи в
        # одной строке оказались бы доллары рядом с рублями без всякого знака.
        header += ["Комиссия площадки", "Продано за", "Валюта закупки",
                   "Валюта продажи", f"Прибыль, {base}", f"ROI, %"]
    header += ["Фото, шт", "В архиве"]

    q = select(Item).where(Item.store_id == store_id)
    if not include_archived:
        q = q.where(Item.archived_at.is_(None))
    rows = (
        await session.execute(q.order_by(Item.created_at.desc(), Item.id))
    ).scalars().all()

    buf = io.StringIO()
    buf.write(BOM)
    w = csv.writer(buf, delimiter=DELIMITER, lineterminator="\r\n",
                   quoting=csv.QUOTE_MINIMAL)
    w.writerow(header)

    for it in rows:
        line: list[str] = [it.sku or "", STATUS_LABELS.get(it.status, it.status.value)]
        for f in columns:
            if f.builtin:
                raw = getattr(it, f.key, None)
                line.append(
                    _num(raw) if f.kind in (FieldKind.NUMBER, FieldKind.MONEY)
                    else _text(raw)
                )
            else:
                line.append(_text((it.extra or {}).get(f.key)))
        line += [
            _date(it.purchase_date), _date(it.listed_date),
            _date(it.sold_date), _date(it.created_at),
        ]
        if show_finance:
            # Суммы в том виде, в каком их вводили, — рядом со своей валютой.
            # Пересчёт в базовую валюту склада в файле не нужен: он зависит от
            # курса и в отчёте только путал бы.
            line += [
                _num(it.cost_price_orig), _num(it.restore_cost_orig),
                _num(it.delivery_cost_orig), _num(it.list_price_orig),
                _num(it.platform_fee_orig), _num(it.selling_price_orig),
                it.cost_currency or "", it.price_currency or "",
                _num(it.net_profit), _num(it.roi_percent),
            ]
        line += [str(len(it.photo_file_ids or [])), "да" if it.archived_at else ""]
        w.writerow(line)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe = "".join(c for c in (store.name or "склад") if c.isalnum() or c in " -_").strip()
    name = f"{safe or 'склад'} {stamp}.csv"
    return name, buf.getvalue().encode("utf-8"), len(rows)


async def send_document(
    chat_id: int, filename: str, data: bytes, caption: str = ""
) -> bool:
    """Отправить файл в личку пользователю.

    Шлём напрямую в Bot API: код исполняется в процессе API, где нет
    диспетчера aiogram, — так же, как при запросах на просмотр склада.
    """
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{API}/sendDocument",
                data={"chat_id": str(chat_id), "caption": caption,
                      "parse_mode": "HTML"},
                files={"document": (filename, data, "text/csv")},
            )
            body = r.json()
            if not body.get("ok"):
                log.warning("выгрузка не доставлена %s: %s", chat_id,
                            body.get("description"))
            return bool(body.get("ok"))
    except Exception as e:  # noqa: BLE001
        log.warning("выгрузка: ошибка отправки: %s", e)
        return False


async def chat_id_of(session: AsyncSession, user_id: uuid.UUID) -> int | None:
    row = (
        await session.execute(select(User.telegram_id).where(User.id == user_id))
    ).scalar_one_or_none()
    return row
