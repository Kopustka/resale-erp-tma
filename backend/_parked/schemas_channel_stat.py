"""Схема статистики каналов — отложена вместе с автопостингом."""

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

