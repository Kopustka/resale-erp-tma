"""Pydantic v2 схемы Request/Response."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from .models import ItemStatus, Role


# --------------------------- Товар --------------------------- #
class ItemBase(BaseModel):
    # Пустое название допустимо: при наличии фото его сгенерирует AI,
    # иначе подставится "бренд категория".
    title: str = Field("", max_length=100)
    brand: str = Field(..., max_length=80)
    category: str = Field(..., max_length=60)
    size: str | None = None
    color: str | None = None
    condition: str | None = None
    # Замеры, см
    length_cm: Decimal | None = None
    width_cm: Decimal | None = None
    sleeve_cm: Decimal | None = None
    description: str | None = None
    purchase_location: str | None = None
    sales_platform: str | None = None
    ad_url: str | None = None


class ItemCreate(ItemBase):
    # Суммы указываются В ВАЛЮТЕ ВВОДА (cost_currency / price_currency),
    # сервер конвертирует в базовую валюту склада.
    cost_price: Decimal = Decimal(0)
    restore_cost: Decimal = Decimal(0)
    delivery_cost: Decimal = Decimal(0)
    list_price: Decimal | None = None  # цена (ассортимента), в price_currency
    cost_currency: str = "BYN"
    price_currency: str = "BYN"
    photo_file_ids: list[str] = Field(default_factory=list)


class ItemUpdate(BaseModel):
    title: str | None = None
    brand: str | None = None
    category: str | None = None
    size: str | None = None
    color: str | None = None
    condition: str | None = None
    length_cm: Decimal | None = None
    width_cm: Decimal | None = None
    sleeve_cm: Decimal | None = None
    description: str | None = None
    cost_price: Decimal | None = None
    restore_cost: Decimal | None = None
    delivery_cost: Decimal | None = None
    platform_fee: Decimal | None = None
    selling_price: Decimal | None = None
    list_price: Decimal | None = None
    cost_currency: str | None = None
    price_currency: str | None = None
    sales_platform: str | None = None
    ad_url: str | None = None


class ItemOut(BaseModel):
    """Полная карточка. Финансовые поля вырезаются на роутере для EMPLOYEE."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    title: str
    brand: str
    category: str
    size: str | None
    color: str | None
    condition: str | None
    length_cm: float | None = None
    width_cm: float | None = None
    sleeve_cm: float | None = None
    description: str | None
    photo_count: int = 0
    status: ItemStatus
    version: int
    listed_date: datetime | None
    sold_date: datetime | None
    created_at: datetime
    # финансы (nullable для EMPLOYEE). float — чистый JSON-number.
    # cost_price/…/selling_price/list_price — ОРИГИНАЛ в валюте ввода.
    cost_price: float | None = None
    restore_cost: float | None = None
    delivery_cost: float | None = None
    platform_fee: float | None = None
    selling_price: float | None = None
    list_price: float | None = None
    cost_currency: str = "BYN"
    price_currency: str = "BYN"
    # значения в базовой валюте склада (для карточек и прибыли)
    cost_price_base: float | None = None
    selling_price_base: float | None = None
    list_price_base: float | None = None
    net_profit: float | None = None
    roi_percent: float | None = None
    purchase_location: str | None = None
    sales_platform: str | None = None


class ItemPage(BaseModel):
    items: list[ItemOut]
    next_cursor: str | None = None


class StatusPatch(BaseModel):
    """Запрос смены статуса при свайпе."""

    target_status: ItemStatus | None = None  # None -> следующий по happy path
    version: int  # ожидаемая версия (optimistic lock)
    selling_price: Decimal | None = None  # обязателен при переходе в SOLD
    selling_currency: str | None = None  # валюта цены продажи (по умолчанию — price_currency)


# --------------------------- AI-описание --------------------------- #
class AiDescribeOut(BaseModel):
    """Сгенерированные название и описание (не сохраняются до подтверждения)."""

    title: str
    description: str


# --------------------------- Голосовой ввод товара --------------------------- #
class VoiceParseRequest(BaseModel):
    text: str


class VoiceParseResult(BaseModel):
    """Поля, извлечённые из голоса, для предзаполнения формы создания."""

    brand: str | None = None
    category: str | None = None
    size: str | None = None
    color: str | None = None
    condition: str | None = None
    cost_price: float | None = None
    list_price: float | None = None
    title: str | None = None
    low_confidence: bool = False


# --------------------------- Аналитика --------------------------- #
class VoiceCaptureOut(BaseModel):
    token: str
    deep_link: str
    expires_in: int


class VoiceCaptureStatus(BaseModel):
    status: str  # waiting | armed | done | expired | error
    fields: VoiceParseResult | None = None
    transcript: str | None = None
    error: str | None = None


class StaleBucket(BaseModel):
    threshold_days: int
    count: int
    item_ids: list[uuid.UUID]


class LocationRoi(BaseModel):
    location: str
    invested: float
    profit: float
    roi_percent: float | None


class TurnoverPoint(BaseModel):
    period: str  # YYYY-MM
    category: str
    avg_days: float
    sold_count: int


class ChannelStat(BaseModel):
    channel_id: uuid.UUID
    chat_id: str
    title: str | None = None
    enabled: bool
    posted: int
    sold: int
    sell_through: float | None = None
    avg_days: float | None = None
    profit: Decimal
    reactions: int = 0


class AnalyticsSummary(BaseModel):
    stale: StaleBucket
    by_location: list[LocationRoi]
    by_channel: list[ChannelStat] = []
    turnover: list[TurnoverPoint]
    total_profit: float
    active_count: int


# --------------------------- Склады / команда --------------------------- #
class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    role: Role
    base_currency: str = "BYN"


class MemberOut(BaseModel):
    user_id: uuid.UUID
    username: str | None
    first_name: str | None
    role: Role


class InviteCreate(BaseModel):
    username: str = Field(..., description="Telegram username без @")
    role: Role = Role.EMPLOYEE


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    username: str
    role: Role
    status: str


class SwitchStore(BaseModel):
    store_id: uuid.UUID


class ChannelSettings(BaseModel):
    channel_id: str | None = None
    channel_signature: str | None = None
    watermark_enabled: bool = False
    watermark_text: str | None = None
    bump_enabled: bool = False
    bump_after_days: int = 60
    preview_before_post: bool = False
    subscriptions_enabled: bool = False
    auto_reply_enabled: bool = False


class ChannelUpdate(BaseModel):
    # @username или -100…; пустая строка/None — отключить автопостинг
    channel_id: str | None = None
    channel_signature: str | None = None
    watermark_enabled: bool | None = None
    watermark_text: str | None = Field(None, max_length=60)
    bump_enabled: bool | None = None
    bump_after_days: int | None = Field(None, ge=7, le=365)
    preview_before_post: bool | None = None
    subscriptions_enabled: bool | None = None
    auto_reply_enabled: bool | None = None


class StoreSettings(BaseModel):
    base_currency: str = "BYN"


class FxRates(BaseModel):
    base: str
    rates: dict[str, float]  # сколько base стоит 1 единица валюты


# --------------------------- Шаблоны постов --------------------------- #
class PostTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    body: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


class PostTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    body: str = Field(..., min_length=1)


class PostTemplateUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=60)
    body: str | None = Field(None, min_length=1)


class TemplatePlaceholder(BaseModel):
    key: str
    label: str
    example: str


class TemplateBriefIn(BaseModel):
    brief: str = Field(..., min_length=3, max_length=1500)


class TemplateGenerated(BaseModel):
    name: str
    body: str


class TemplatePreviewIn(BaseModel):
    body: str = Field(..., min_length=1)


class TemplatePreviewOut(BaseModel):
    caption: str


class CaptureSessionOut(BaseModel):
    token: str
    deep_link: str
    expires_in: int


class CaptureStatusOut(BaseModel):
    status: str  # waiting | armed | done | expired | error
    template_id: uuid.UUID | None = None
    error: str | None = None


# --------------------------- Каналы автопостинга --------------------------- #
class ChannelOut(BaseModel):
    id: uuid.UUID
    chat_id: str
    title: str | None = None
    signature: str | None = None
    enabled: bool
    posts_count: int = 0


class ChannelCreate(BaseModel):
    chat_id: str = Field(..., min_length=1, max_length=80)
    title: str | None = Field(None, max_length=120)
    signature: str | None = Field(None, max_length=120)


class ChannelUpdateOne(BaseModel):
    title: str | None = Field(None, max_length=120)
    signature: str | None = Field(None, max_length=120)
    enabled: bool | None = None


# --------------------------- Подборки (дропы) --------------------------- #
class DropCreate(BaseModel):
    item_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=10)
    title: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=500)


class DropOut(BaseModel):
    id: uuid.UUID
    title: str | None = None
    item_count: int
    with_photo: int
    channels: int


# --------------------------- Свободные посты --------------------------- #
class CustomPostCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    photo_file_ids: list[str] = Field(default_factory=list, max_length=10)
    # None — опубликовать сразу
    scheduled_at: datetime | None = None


class CustomPostOut(BaseModel):
    id: uuid.UUID
    body: str
    photo_count: int
    scheduled_at: datetime | None = None
    status: str
    created_at: datetime
