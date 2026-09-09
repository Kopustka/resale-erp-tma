"""Запись действий участников склада в общий журнал.

Смены статусов здесь не пишутся — они уже лежат в item_status_logs, и
дублировать их значит показывать в ленте каждое событие дважды. Лента
админки склеивает обе таблицы при чтении.

Правило вызова: `record()` только добавляет строку в текущую сессию, без
flush и commit. Запись уезжает в базу вместе с самой операцией, поэтому в
журнале не может оказаться действия, которое откатилось.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog

# Коды действий. Держим здесь, а не строками по коду: подпись в ленте
# берётся по этому же ключу, и опечатка в вызове иначе всплыла бы только
# у владельца в интерфейсе.
ITEM_CREATE = "item.create"
ITEM_EDIT = "item.edit"
ITEM_DELETE = "item.delete"
ITEM_RESTORE = "item.restore"
ITEM_PHOTO = "item.photo"
ITEM_AI_DESCRIBE = "item.ai_describe"

DISCOUNT_CREATE = "discount.create"
DISCOUNT_CANCEL = "discount.cancel"

POST_CREATE = "post.create"
POST_CANCEL = "post.cancel"
DROP_CREATE = "drop.create"

TEMPLATE_CREATE = "template.create"
TEMPLATE_EDIT = "template.edit"
TEMPLATE_DELETE = "template.delete"
TEMPLATE_DEFAULT = "template.default"

CHANNEL_ADD = "channel.add"
CHANNEL_EDIT = "channel.edit"
CHANNEL_DELETE = "channel.delete"

SETTINGS_EDIT = "settings.edit"
MEMBER_INVITE = "member.invite"
MEMBER_REMOVE = "member.remove"
MEMBER_REVOKE = "member.revoke"

#: Человекочитаемая подпись + значок для ленты. Ключи совпадают с кодами выше.
LABELS: dict[str, tuple[str, str]] = {
    ITEM_CREATE: ("➕", "Добавил вещь"),
    ITEM_EDIT: ("✏️", "Изменил вещь"),
    ITEM_DELETE: ("🗑", "Удалил вещь"),
    ITEM_RESTORE: ("♻️", "Восстановил вещь"),
    ITEM_PHOTO: ("🖼", "Загрузил фото"),
    ITEM_AI_DESCRIBE: ("🤖", "Сгенерировал описание"),
    DISCOUNT_CREATE: ("🏷", "Создал скидку"),
    DISCOUNT_CANCEL: ("🚫", "Отменил скидку"),
    POST_CREATE: ("📝", "Запланировал пост"),
    POST_CANCEL: ("🚫", "Отменил пост"),
    DROP_CREATE: ("📦", "Создал дроп"),
    TEMPLATE_CREATE: ("🧩", "Создал шаблон"),
    TEMPLATE_EDIT: ("🧩", "Изменил шаблон"),
    TEMPLATE_DELETE: ("🧩", "Удалил шаблон"),
    TEMPLATE_DEFAULT: ("⭐️", "Сменил активный шаблон"),
    CHANNEL_ADD: ("📢", "Подключил канал"),
    CHANNEL_EDIT: ("📢", "Изменил канал"),
    CHANNEL_DELETE: ("📢", "Отключил канал"),
    SETTINGS_EDIT: ("⚙️", "Изменил настройки"),
    MEMBER_INVITE: ("👤", "Пригласил участника"),
    MEMBER_REMOVE: ("🚪", "Исключил участника"),
    MEMBER_REVOKE: ("👤", "Отозвал приглашение"),
}

#: Действия, которые владелец обычно хочет отфильтровать отдельно.
GROUPS: dict[str, str] = {
    "items": "Вещи",
    "status": "Статусы",
    "publishing": "Публикации",
    "settings": "Настройки",
}

_GROUP_BY_PREFIX = {
    "item": "items",
    "discount": "publishing",
    "post": "publishing",
    "drop": "publishing",
    "template": "settings",
    "channel": "settings",
    "settings": "settings",
    "member": "settings",
}


#: Все описанные префиксы — по ним отличаем «прочее» от известных групп.
ALL_PREFIXES = tuple(sorted(_GROUP_BY_PREFIX))


def prefixes_of(group: str) -> list[str]:
    """Префиксы действий, попадающие в группу.

    Нужны, чтобы фильтровать ленту в SQL, а не после LIMIT: иначе страница
    из пятидесяти настроечных записей превращалась в пустой ответ «событий
    по вещам нет», хотя их сотни.
    """
    known = {p for p, g in _GROUP_BY_PREFIX.items() if g == group}
    if group == "items":
        # «items» — ещё и всё, чей префикс не описан явно.
        return sorted(known | {"item"})
    return sorted(known)


def group_of(action: str) -> str:
    """Группа действия для фильтра в ленте."""
    if action == "status":
        return "status"
    return _GROUP_BY_PREFIX.get(action.split(".", 1)[0], "items")


def label_of(action: str) -> tuple[str, str]:
    """Значок и подпись; для неизвестного кода — сам код, чтобы не терять запись."""
    return LABELS.get(action, ("•", action))


def record(
    session: AsyncSession,
    *,
    store_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: str,
    summary: str = "",
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> None:
    """Добавить запись в журнал (без commit — уедет с транзакцией вызова)."""
    session.add(
        AuditLog(
            store_id=store_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=summary[:300],
        )
    )
