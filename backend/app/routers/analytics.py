"""BI-аналитика (только OWNER/ANALYST)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import CAN_SEE_FINANCE, require_role
from ..config import get_settings
from ..db import get_session
from ..models import StoreMember
from ..repositories.analytics import AnalyticsRepository
from ..schemas import (
    AnalyticsSummary,
    LocationRoi,
    StaleBucket,
    TurnoverPoint,
)
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends as _D

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])
settings = get_settings()


@router.get("/summary", response_model=AnalyticsSummary)
async def summary(
    member: StoreMember = Depends(require_role(*CAN_SEE_FINANCE)),
    session: AsyncSession = _D(get_session),
):
    repo = AnalyticsRepository(session)
    store_id = member.store_id
    days = settings.stale_days_threshold

    stale_count, stale_ids = await repo.stale_items(store_id, days)
    locations = await repo.roi_by_location(store_id)
    turnover = await repo.turnover(store_id)
    total = await repo.total_profit(store_id)
    active = await repo.active_count(store_id)

    return AnalyticsSummary(
        stale=StaleBucket(threshold_days=days, count=stale_count, item_ids=stale_ids),
        by_channel=await repo.by_channel(member.store_id),
        by_location=[LocationRoi(**r) for r in locations],
        turnover=[TurnoverPoint(**t) for t in turnover],
        total_profit=total,
        active_count=active,
    )
