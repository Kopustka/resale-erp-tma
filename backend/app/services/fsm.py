"""Конечный автомат статусов товара."""
from ..models import ItemStatus

S = ItemStatus

# Матрица разрешённых переходов: прямые (happy path) + аварийные/обратные
# + откат на шаг назад (случайный свайп можно отменить).
ALLOWED_TRANSITIONS: dict[ItemStatus, set[ItemStatus]] = {
    S.BOUGHT: {S.PREPARING, S.PHOTOGRAPHED, S.CANCELLED},
    S.PREPARING: {S.PHOTOGRAPHED, S.CANCELLED, S.BOUGHT},
    S.PHOTOGRAPHED: {S.LISTED, S.CANCELLED, S.PREPARING},
    S.LISTED: {S.BOOKED, S.SOLD, S.CANCELLED, S.PHOTOGRAPHED},
    S.BOOKED: {S.SOLD, S.LISTED, S.CANCELLED},   # бронь может сорваться → снова LISTED
    S.SOLD: {S.SHIPPED, S.RETURNED, S.CANCELLED, S.BOOKED},
    S.SHIPPED: {S.COMPLETED, S.RETURNED, S.SOLD},
    S.COMPLETED: {S.RETURNED, S.SHIPPED},
    S.RETURNED: {S.LISTED, S.PREPARING},         # вернули → снова в оборот
    S.CANCELLED: {S.LISTED},                      # реанимация отменённого
}

# Откат на шаг назад по happy path (отмена случайного перехода).
PREV_STATUS: dict[ItemStatus, ItemStatus] = {
    S.PREPARING: S.BOUGHT,
    S.PHOTOGRAPHED: S.PREPARING,
    S.LISTED: S.PHOTOGRAPHED,
    S.BOOKED: S.LISTED,
    S.SOLD: S.BOOKED,
    S.SHIPPED: S.SOLD,
    S.COMPLETED: S.SHIPPED,
}

# Статусы «до листинга» и «до продажи» — при откате в них сбрасываются даты.
PRE_LISTED = {S.BOUGHT, S.PREPARING, S.PHOTOGRAPHED}
PRE_SOLD = PRE_LISTED | {S.LISTED, S.BOOKED}

# «Следующий логический статус» для свайпа вправо (happy path).
NEXT_STATUS: dict[ItemStatus, ItemStatus] = {
    S.BOUGHT: S.PREPARING,
    S.PREPARING: S.PHOTOGRAPHED,
    S.PHOTOGRAPHED: S.LISTED,
    S.LISTED: S.BOOKED,
    S.BOOKED: S.SOLD,
    S.SOLD: S.SHIPPED,
    S.SHIPPED: S.COMPLETED,
}

# Статусы, исключаемые из прибыли/аналитики продаж.
NON_REALIZED = {S.RETURNED, S.CANCELLED}
SOLD_STATUSES = {S.SOLD, S.SHIPPED, S.COMPLETED}


def can_transition(current: ItemStatus, target: ItemStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


def next_status(current: ItemStatus) -> ItemStatus | None:
    return NEXT_STATUS.get(current)


def prev_status(current: ItemStatus) -> ItemStatus | None:
    return PREV_STATUS.get(current)
