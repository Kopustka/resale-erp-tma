"""Модели SQLAlchemy 2.0.

Инвариант домена: каждый Item — физически уникальная вещь (quantity = 1).
Все деньги — Numeric в единой валюте (без мультивалютности).
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


# --------------------------------------------------------------------------- #
# Перечисления
# --------------------------------------------------------------------------- #
class ItemStatus(str, enum.Enum):
    """Пять состояний вещи.

    Значения BOOKED/SOLD/COMPLETED/CANCELLED/RETURNED убраны из домена.
    В типе job_kind_enum PostgreSQL их метки остаются (удалять значения из
    enum СУБД нельзя), но строк с ними в таблицах нет — перенесены миграцией.
    """

    BOUGHT = "BOUGHT"              # Куплен
    PREPARING = "PREPARING"        # Подготовка (стирка/ремонт)
    PHOTOGRAPHED = "PHOTOGRAPHED"  # Сфотографирован
    LISTED = "LISTED"              # Выставлен (тикает счётчик дней)
    SHIPPED = "SHIPPED"            # Отправлен = продан


class Role(str, enum.Enum):
    OWNER = "OWNER"
    EMPLOYEE = "EMPLOYEE"
    ANALYST = "ANALYST"


class InviteStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REVOKED = "REVOKED"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --------------------------------------------------------------------------- #
# Склады / пользователи / членство
# --------------------------------------------------------------------------- #
class Store(Base):
    __tablename__ = "stores"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        # use_alter разрывает циклическую зависимость stores<->users для DDL
        ForeignKey("users.id", use_alter=True, name="fk_stores_owner"),
        nullable=True,
    )
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Minsk")
    # Основная валюта учёта склада (всё сводится к ней). BYN по умолчанию.
    base_currency: Mapped[str] = mapped_column(String(3), default="BYN")
    # Автопостинг: канал (@username или -100… id) и контакт-подпись под постами.
    channel_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    channel_signature: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Водяной знак на фото, уходящих в канал (оригиналы не меняются).
    watermark_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    watermark_text: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Автоподнятие зависших: по умолчанию выключено — оно удаляет старый пост.
    bump_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    bump_after_days: Mapped[int] = mapped_column(Integer, default=60)
    # Показывать пост в личке и ждать кнопки «Опубликовать».
    preview_before_post: Mapped[bool] = mapped_column(Boolean, default=False)
    # Приём подписок «сообщи, когда появится» от покупателей.
    subscriptions_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Автоответы на типовые вопросы в комментариях под постом.
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Шаблон объявления о скидке. NULL — встроенный.
    discount_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    members: Mapped[list["StoreMember"]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )


class User(Base):
    """Идентичность пользователя. Роль здесь НЕ хранится — она в store_members."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), index=True)
    first_name: Mapped[str | None] = mapped_column(String(120))
    language_code: Mapped[str | None] = mapped_column(String(8))
    current_store_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), nullable=True
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    memberships: Mapped[list["StoreMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class StoreMember(Base):
    """Связка user↔store↔role. Роль привязана к паре, а не к юзеру."""

    __tablename__ = "store_members"
    __table_args__ = (
        UniqueConstraint("user_id", "store_id", name="uq_member_user_store"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), index=True
    )
    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), index=True
    )
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="role_enum"))
    created_at: Mapped[datetime] = _created()

    user: Mapped[User] = relationship(back_populates="memberships")
    store: Mapped[Store] = relationship(back_populates="members")


class StoreInvite(Base):
    """Приглашение по юзернейму до первого контакта с ботом."""

    __tablename__ = "store_invites"
    __table_args__ = (
        Index("ix_invite_username_status", "username", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), index=True
    )
    username: Mapped[str] = mapped_column(String(64), index=True)  # lower, без @
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="role_enum", create_type=False))
    invited_by: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id")
    )
    status: Mapped[InviteStatus] = mapped_column(
        SAEnum(InviteStatus, name="invite_status_enum"),
        default=InviteStatus.PENDING,
    )
    created_at: Mapped[datetime] = _created()
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class StoreCounter(Base):
    """Монотонный счётчик SKU per-store (обновляется под FOR UPDATE)."""

    __tablename__ = "store_counters"

    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), primary_key=True
    )
    last_sku: Mapped[int] = mapped_column(Integer, default=1000)


# --------------------------------------------------------------------------- #
# Товар и аудит
# --------------------------------------------------------------------------- #
class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        Index("ix_items_store_status_arch", "store_id", "status", "archived_at"),
        Index("ix_items_store_brand", "store_id", "brand"),
        Index("ix_items_store_sold", "store_id", "sold_date"),
        Index("ix_items_store_listed", "store_id", "listed_date"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), index=True
    )
    sku: Mapped[str] = mapped_column(String(16))

    # Мета
    title: Mapped[str] = mapped_column(String(100))
    brand: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(60))
    size: Mapped[str | None] = mapped_column(String(20))
    color: Mapped[str | None] = mapped_column(String(40))
    condition: Mapped[str | None] = mapped_column(String(8))  # "8/10"
    # Замеры, см (стандарт ресейла: длина, ширина pit-to-pit, рукав)
    length_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    width_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    sleeve_cm: Mapped[Decimal | None] = mapped_column(Numeric(5, 1), nullable=True)
    description: Mapped[str | None] = mapped_column(Text)
    photo_file_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default="{}"
    )

    # Финансы. Основные колонки cost_price/…/selling_price хранят суммы В БАЗОВОЙ
    # ВАЛЮТЕ склада (конвертируются при сохранении) — по ним считаются прибыль и
    # аналитика. Рядом храним ОРИГИНАЛ ввода и его валюту (для формы и постов).
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    restore_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    delivery_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    platform_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    selling_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    list_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)  # база

    # Валюты ввода: cost_currency — для закупочных сумм, price_currency — для цен продажи.
    cost_currency: Mapped[str] = mapped_column(String(3), default="BYN")
    price_currency: Mapped[str] = mapped_column(String(3), default="BYN")
    # Оригиналы сумм в валюте ввода (для формы; база — в колонках выше).
    cost_price_orig: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    restore_cost_orig: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    delivery_cost_orig: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    platform_fee_orig: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    selling_price_orig: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    list_price_orig: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Локации / площадки
    purchase_location: Mapped[str | None] = mapped_column(String(120))
    purchase_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purchaser_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    sales_platform: Mapped[str | None] = mapped_column(String(40))
    ad_url: Mapped[str | None] = mapped_column(String(300))
    listed_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Статус / служебное
    status: Mapped[ItemStatus] = mapped_column(
        SAEnum(ItemStatus, name="item_status_enum"),
        default=ItemStatus.BOUGHT,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=0)  # optimistic lock
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # id опубликованного поста в канале (дедуп + пометка «продано»)
    channel_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Цена до последней скидки: нужна, чтобы показать зачёркнутый старый
    # ценник. Без отдельного поля она терялась бы при перезаписи list_price.
    price_before_discount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    # Когда вещь последний раз поднимали в канале (защита от бампа по кругу).
    bumped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    # ------------------ вычисляемые поля (без хранения) ------------------ #
    @hybrid_property
    def total_invested(self) -> Decimal:
        return (
            (self.cost_price or Decimal(0))
            + (self.restore_cost or Decimal(0))
            + (self.delivery_cost or Decimal(0))
        )

    @hybrid_property
    def net_profit(self) -> Decimal | None:
        if self.selling_price is None:
            return None
        return (
            self.selling_price
            - self.total_invested
            - (self.platform_fee or Decimal(0))
        )

    @net_profit.expression  # type: ignore[no-redef]
    def net_profit(cls):
        return cls.selling_price - (
            cls.cost_price + cls.restore_cost + cls.delivery_cost + cls.platform_fee
        )

    @hybrid_property
    def roi_percent(self) -> Decimal | None:
        invested = self.total_invested
        profit = self.net_profit
        if profit is None or invested == 0:
            return None
        return (profit / invested) * Decimal(100)


class ItemStatusLog(Base):
    """Аудит смены статусов — источник правды для аналитики оборачиваемости."""

    __tablename__ = "item_status_logs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("items.id"), index=True
    )
    old_status: Mapped[ItemStatus | None] = mapped_column(
        SAEnum(ItemStatus, name="item_status_enum", create_type=False), nullable=True
    )
    new_status: Mapped[ItemStatus] = mapped_column(
        SAEnum(ItemStatus, name="item_status_enum", create_type=False)
    )
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = _created()


class PostTemplate(Base):
    """Шаблон подписи поста для автопостинга в канал.

    Активный шаблон помечен is_default (ровно один на склад). Флаг живёт
    здесь, а не колонкой в stores, потому что схема поднимается через
    Base.metadata.create_all: он создаёт новые таблицы, но не добавляет
    колонки в существующие — новой таблицей миграция не нужна.
    """

    __tablename__ = "post_templates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    # Тело с плейсхолдерами {title}, {price}… Может содержать HTML-разметку Telegram.
    body: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # Исходный пример поста, с которого нейросеть склонировала дизайн (если клонировали).
    source_sample: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (
        Index("ix_tpl_store_default", "store_id", "is_default"),
    )


class JobKind(str, enum.Enum):
    POST_ITEM = "POST_ITEM"      # опубликовать вещь в канал
    MARK_SOLD = "MARK_SOLD"      # пометить существующий пост проданным
    EDIT_CAPTION = "EDIT_CAPTION"  # перерисовать подпись (сменилась цена и т.п.)
    BUMP = "BUMP"                # поднять зависшую вещь: удалить пост и дать заново
    NOTIFY_SUB = "NOTIFY_SUB"    # уведомить подписчика о подходящей новинке
    DISCOUNT_POST = "DISCOUNT_POST"  # объявить скидку ответом на пост вещи
    DROP_POST = "DROP_POST"      # опубликовать несколько вещей одним альбомом
    CUSTOM_POST = "CUSTOM_POST"  # свободный пост без привязки к вещи
    UNPUBLISH = "UNPUBLISH"      # снять вещь с публикации (откат из «выставлен»)


class JobStatus(str, enum.Enum):
    # Ждёт подтверждения владельца в личке — воркер такие не забирает.
    AWAITING = "AWAITING"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PostJob(Base):
    """Задание на публикацию в канал.

    Раньше постинг уходил в asyncio.create_task: задача не переживала
    рестарт, не имела ретраев и не считалась с лимитами Telegram
    (~20 сообщений в минуту на канал). Очередь в БД решает всё три задачи
    и даёт отложенную публикацию.
    """

    __tablename__ = "post_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), index=True
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("items.id"), nullable=True, index=True
    )
    kind: Mapped[JobKind] = mapped_column(SAEnum(JobKind, name="job_kind_enum"))
    status: Mapped[JobStatus] = mapped_column(
        SAEnum(JobStatus, name="job_status_enum"), default=JobStatus.PENDING
    )
    channel_id: Mapped[str] = mapped_column(String(80))
    # Ссылка на канал: нужна, чтобы записать ItemPost после публикации.
    channel_uid: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    # Какую скидку объявляем (для DISCOUNT_POST).
    discount_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    # Какой свободный пост публикуем (для CUSTOM_POST).
    custom_post_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    # Какой дроп публикуем (для DROP_POST).
    drop_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    # Кому уведомление (для NOTIFY_SUB) — нужна для кнопки отписки.
    sub_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
    # Плашка над подписью при перерисовке (например, «🔥 СКИДКА»).
    caption_prefix: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Для MARK_SOLD / EDIT_CAPTION — какой пост править.
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    # Не раньше этого момента: отложенная публикация и экспоненциальный откат.
    run_after: Mapped[datetime] = _created()
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after"),
        Index("ix_jobs_store_status", "store_id", "status"),
    )


class Channel(Base):
    """Канал автопостинга. Складов много, каналов у склада — тоже.

    Пришёл на смену полям stores.channel_id / channel_signature: они
    остаются как legacy и синхронизируются с основным каналом ради
    совместимости со старым API.
    """

    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), index=True
    )
    # @username или числовой -100…
    chat_id: Mapped[str] = mapped_column(String(80))
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    __table_args__ = (
        UniqueConstraint("store_id", "chat_id", name="uq_channel_store_chat"),
    )


class ItemPost(Base):
    """Опубликованный пост вещи в конкретном канале.

    Заменяет одиночное Item.channel_message_id: с несколькими каналами
    нужно знать, какое сообщение править при продаже в каждом из них.
    """

    __tablename__ = "item_posts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("items.id"), index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("channels.id"), index=True
    )
    message_id: Mapped[int] = mapped_column(BigInteger)
    sold_marked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Число реакций на пост. Просмотры Bot API не отдаёт (только MTProto),
    # поэтому реакции — единственный доступный отсюда сигнал отклика.
    reactions: Mapped[int] = mapped_column(Integer, default=0)
    # Привязка к ветке комментариев: канальный пост автоматически пересылается
    # в группу обсуждений, и ответы висят тредом на этой копии.
    discussion_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discussion_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        UniqueConstraint("item_id", "channel_id", name="uq_post_item_channel"),
    )


class Subscription(Base):
    """Подписка покупателя на появление подходящих вещей.

    Подписчик может не быть пользователем системы — он просто человек из
    канала, поэтому храним telegram_id, а не ссылку на users.
    Пустой фильтр означает «любой»: подписка только на бренд ловит все
    размеры этого бренда.
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), index=True
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)

    brand: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category: Mapped[str | None] = mapped_column(String(60), nullable=True)
    size: Mapped[str | None] = mapped_column(String(20), nullable=True)
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()
    last_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_sub_store_active", "store_id", "active"),
    )


class Drop(Base):
    """Подборка вещей, публикуемая одним альбомом.

    Telegram кладёт в медиагруппу не больше 10 файлов, поэтому берём по
    одному фото с вещи и ограничиваем состав.
    """

    __tablename__ = "drops"

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created()


class DropItem(Base):
    """Вещь в подборке. position задаёт порядок в альбоме."""

    __tablename__ = "drop_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    drop_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("drops.id"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("items.id"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("drop_id", "item_id", name="uq_drop_item"),
    )


class CustomPostStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"


class CustomPost(Base):
    """Пост без привязки к вещи: анонс, опрос, «завтра ресток».

    Канал живёт не только карточками товара, а расписание уже умеет
    очередь — публикация ставится заданием с run_after.
    """

    __tablename__ = "custom_posts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    photo_file_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default="{}"
    )
    # NULL — публиковать сразу.
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[CustomPostStatus] = mapped_column(
        SAEnum(CustomPostStatus, name="custom_post_status_enum"),
        default=CustomPostStatus.SCHEDULED,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        Index("ix_custom_store_when", "store_id", "scheduled_at"),
    )


class DiscountStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"


class Discount(Base):
    """Скидка на вещь: сразу или по расписанию.

    Хранит цену до и после — по ним считается процент и рисуется
    зачёркнутый старый ценник, даже если вещь потом подешевеет ещё раз.
    """

    __tablename__ = "discounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    store_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("stores.id"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("items.id"), index=True
    )
    old_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    new_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="BYN")
    # NULL — публикуем сразу.
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[DiscountStatus] = mapped_column(
        SAEnum(DiscountStatus, name="discount_status_enum"),
        default=DiscountStatus.SCHEDULED,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        Index("ix_discount_store_when", "store_id", "scheduled_at"),
    )
