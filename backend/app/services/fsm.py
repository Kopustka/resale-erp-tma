"""Конечный автомат статусов товара.

Пять состояний: куплен -> подготовка -> отфотографирован -> выставлен ->
отправлен. «Отправлен» — терминал и одновременно признак продажи: именно
от него считаются прибыль, окупаемость и оборачиваемость.
"""
from ..models import ItemStatus

S = ItemStatus

# Матрица переходов: шаг вперёд по цепочке и шаг назад (случайный свайп
# должен отменяться). Прыжки через этап разрешены только вперёд — из
# «куплен» сразу в «отфотографирован», если подготовка не нужна.
ALLOWED_TRANSITIONS: dict[ItemStatus, set[ItemStatus]] = {
    S.BOUGHT: {S.PREPARING, S.PHOTOGRAPHED},
    S.PREPARING: {S.PHOTOGRAPHED, S.BOUGHT},
    S.PHOTOGRAPHED: {S.LISTED, S.PREPARING},
    S.LISTED: {S.SHIPPED, S.PHOTOGRAPHED},
    S.SHIPPED: {S.LISTED},  # ошиблись с отправкой — вернуть в продажу
}

# Откат на шаг назад по основной цепочке.
PREV_STATUS: dict[ItemStatus, ItemStatus] = {
    S.PREPARING: S.BOUGHT,
    S.PHOTOGRAPHED: S.PREPARING,
    S.LISTED: S.PHOTOGRAPHED,
    S.SHIPPED: S.LISTED,
}

# Статусы «до листинга» и «до продажи» — при откате в них сбрасываются даты.
PRE_LISTED = {S.BOUGHT, S.PREPARING, S.PHOTOGRAPHED}
PRE_SOLD = PRE_LISTED | {S.LISTED}

# «Следующий логический статус» для свайпа вправо.
NEXT_STATUS: dict[ItemStatus, ItemStatus] = {
    S.BOUGHT: S.PREPARING,
    S.PREPARING: S.PHOTOGRAPHED,
    S.PHOTOGRAPHED: S.LISTED,
    S.LISTED: S.SHIPPED,
}

# Статусов «сделка не состоялась» больше нет: возврат и отмена убраны.
NON_REALIZED: set[ItemStatus] = set()
# Признак продажи. Раньше сюда входили SOLD и COMPLETED — теперь их нет,
# и «отправлен» стал единственным состоянием проданной вещи.
SOLD_STATUSES = {S.SHIPPED}


def can_transition(current: ItemStatus, target: ItemStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


def next_status(current: ItemStatus) -> ItemStatus | None:
    return NEXT_STATUS.get(current)


def prev_status(current: ItemStatus) -> ItemStatus | None:
    return PREV_STATUS.get(current)
