"""Аутентификация Telegram WebApp initData + зависимости ролей."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import Role, StoreInvite, StoreMember, User, InviteStatus

settings = get_settings()


def _validate_init_data(init_data: str) -> dict:
    """Проверяет подпись и свежесть initData, возвращает распарсенные поля."""
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Malformed initData")

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No hash in initData")

    # Каноническая data_check_string
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )
    secret_key = hmac.new(
        b"WebAppData", settings.bot_token.encode(), hashlib.sha256
    ).digest()
    calc_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calc_hash, received_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Bad initData signature")

    # Защита от replay: auth_date не старше TTL
    auth_date = int(parsed.get("auth_date", "0"))
    if settings.init_data_ttl and (time.time() - auth_date) > settings.init_data_ttl:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "initData expired")

    return parsed


async def _accept_pending_invites(session: AsyncSession, user: User) -> None:
    """При контакте — привязать юзера к складам из pending-приглашений по username."""
    if not user.username:
        return
    uname = user.username.lower()
    invites = (
        await session.execute(
            select(StoreInvite).where(
                StoreInvite.username == uname,
                StoreInvite.status == InviteStatus.PENDING,
            )
        )
    ).scalars().all()
    for inv in invites:
        exists = (
            await session.execute(
                select(StoreMember).where(
                    StoreMember.user_id == user.id,
                    StoreMember.store_id == inv.store_id,
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                StoreMember(user_id=user.id, store_id=inv.store_id, role=inv.role)
            )
        inv.status = InviteStatus.ACCEPTED
        if user.current_store_id is None:
            user.current_store_id = inv.store_id


async def get_current_user(
    x_tg_init_data: str = Header(..., alias="X-TG-Init-Data"),
    session: AsyncSession = Depends(get_session),
) -> User:
    parsed = _validate_init_data(x_tg_init_data)
    tg_user = json.loads(parsed.get("user", "{}"))
    tg_id = tg_user.get("id")
    if not tg_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No user in initData")

    user = (
        await session.execute(select(User).where(User.telegram_id == tg_id))
    ).scalar_one_or_none()

    if user is None:
        user = User(
            telegram_id=tg_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
            language_code=tg_user.get("language_code"),
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            # Два первых запроса от одного человека пришли одновременно —
            # обычное дело, когда мини-апп открывают из бота двойным тапом.
            # Проигравший просто перечитывает то, что успел создать первый.
            await session.rollback()
            return (
                await session.execute(select(User).where(User.telegram_id == tg_id))
            ).scalar_one()
        await _accept_pending_invites(session, user)
        await session.commit()
    else:
        # Юзернейм мог смениться — обновляем и добираем возможные новые инвайты
        new_username = tg_user.get("username")
        if new_username and new_username != user.username:
            user.username = new_username
            await _accept_pending_invites(session, user)
            await session.commit()

    await session.refresh(user)
    return user


async def get_active_membership(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StoreMember:
    """Членство юзера на его активном складе."""
    if user.current_store_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No active store")
    member = (
        await session.execute(
            select(StoreMember).where(
                StoreMember.user_id == user.id,
                StoreMember.store_id == user.current_store_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of active store")
    return member


def require_role(*allowed: Role):
    """Фабрика зависимостей: пускает только перечисленные роли."""

    async def _dep(member: StoreMember = Depends(get_active_membership)) -> StoreMember:
        if member.role not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Role {member.role.value} not allowed",
            )
        return member

    return _dep


# Готовые пресеты
CAN_EDIT = (Role.OWNER, Role.EMPLOYEE)          # операционка
CAN_SEE_FINANCE = (Role.OWNER, Role.ANALYST)    # деньги/аналитика
OWNER_ONLY = (Role.OWNER,)
