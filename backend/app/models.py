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
    BOUGHT = "BOUGHT"            # Куплен
    PREPARING = "PREPARING"     # Подготовка (стирка/ремонт)
    PHOTOGRAPHED = "PHOTOGRAPHED"  # Сфотографирован
    LISTED = "LISTED"           # Выставлен (тикает счётчик дней)
    BOOKED = "BOOKED"           # Забронирован
    SOLD = "SOLD"               # Продан
    SHIPPED = "SHIPPED"         # Отправлен
    COMPLETED = "COMPLETED"     # Завершён
    CANCELLED = "CANCELLED"     # Отменён (сделка сорвалась)
    RETURNED = "RETURNED"       # Возврат


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
