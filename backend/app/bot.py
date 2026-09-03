"""aiogram 3.x бот: WebApp-кнопка, /start (активация инвайтов), приём фото, CSV."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from io import BytesIO
from contextlib import suppress

from aiogram import Bot, Dispatcher, F
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    MessageReactionCountUpdated,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from aiogram.utils.text_decorations import html_decoration
from sqlalchemy import or_, select, update

from .config import get_settings
from .db import SessionLocal
from .models import (
    InviteStatus,
    Item,
    ItemStatus,
    OversightStatus,
    Role,
    Store,
    StoreInvite,
    StoreMember,
    StoreOversight,
    User,
)
from .services import oversight
from .repositories.items import ItemRepository
from .services.fsm import prev_status

settings = get_settings()
bot = Bot(token=settings.bot_token)
dp = Dispatcher()


def _webapp_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧥 Открыть склад",
                    web_app=WebAppInfo(url=settings.webapp_url),
                )
            ]
        ]
    )


async def _activate_invites(session, user: User) -> int:
    """Привязать юзера к складам из pending-инвайтов по его username."""
    if not user.username:
        return 0
    uname = user.username.lower()
    invites = (
        await session.execute(
            select(StoreInvite).where(
                StoreInvite.username == uname,
                StoreInvite.status == InviteStatus.PENDING,
            )
        )
    ).scalars().all()
    activated = 0
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
            if user.current_store_id is None:
                user.current_store_id = inv.store_id
            activated += 1
        inv.status = InviteStatus.ACCEPTED
    return activated


def _read_local_photo(entry: str) -> bytes | None:
    from pathlib import Path

    name = entry[len("local:"):]
    if "/" in name or "\\" in name or ".." in name:
        return None
    p = Path(settings.media_dir) / name
    return p.read_bytes() if p.exists() else None


@dp.message(CommandStart())
async def cmd_start(message: Message):
    tg = message.from_user
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == tg.id))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=tg.id,
                username=tg.username,
                first_name=tg.first_name,
                language_code=tg.language_code,
            )
            session.add(user)
            await session.flush()
        else:
            user.username = tg.username

        activated = await _activate_invites(session, user)

        # Если совсем нет складов — создаём личный склад, юзер = OWNER
        has_store = (
            await session.execute(
                select(StoreMember).where(StoreMember.user_id == user.id).limit(1)
            )
        ).scalar_one_or_none()
        if has_store is None and activated == 0:
            store = Store(name=f"Склад {tg.first_name or 'мой'}", owner_id=user.id)
            session.add(store)
            await session.flush()
            session.add(
                StoreMember(user_id=user.id, store_id=store.id, role=_owner_role())
            )
            user.current_store_id = store.id

        # Склад появился только что — теперь есть к чему привязать записи
        # наблюдения, созданные до первого запуска бота.
        await oversight.bind_pending_for(session, user)

        await session.commit()

    greeting = "Добро пожаловать в ресейл-ERP!"
    if activated:
        greeting = f"Вы добавлены на {activated} склад(ов) по приглашению."
    await message.answer(greeting, reply_markup=_webapp_kb())


def _owner_role():
    from .models import Role
    return Role.OWNER


# Кнопка «Закрыть доступ» под сообщением команды /nadzor.
REVOKE = "ovrm"


@dp.callback_query(F.data.startswith(f"{REVOKE}:"))
async def on_oversight_revoke(cq: CallbackQuery):
    """Закрыть ранее открытый доступ к своей ленте."""
    try:
        req_id = uuid.UUID((cq.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await cq.answer("Некорректная кнопка")
        return

    async with SessionLocal() as s:
        user = (
            await s.execute(select(User).where(User.telegram_id == cq.from_user.id))
        ).scalar_one_or_none()
        req = (
            await s.execute(select(StoreOversight).where(StoreOversight.id == req_id))
        ).scalar_one_or_none()
        if user is None or req is None:
            await cq.answer("Не найдено")
            return
        if not user.username or user.username.lower() != req.target_username:
            await cq.answer("Это не ваш доступ")
            return
        req.status = OversightStatus.REVOKED
        await s.commit()

    await cq.answer("Доступ закрыт")
    with suppress(Exception):
        await cq.message.edit_reply_markup(reply_markup=None)
    await cq.message.answer("🔒 Доступ закрыт. Лента больше не видна.")


@dp.message(Command("nadzor", "надзор"))
async def cmd_nadzor(message: Message):
    """Кому открыта лента моего склада — и кнопки, чтобы закрыть."""
    tg = message.from_user
    async with SessionLocal() as s:
        user = (
            await s.execute(select(User).where(User.telegram_id == tg.id))
        ).scalar_one_or_none()
        if user is None or not user.username:
            await message.answer("Сначала нажмите /start.")
            return
        rows = (
            await s.execute(
                select(StoreOversight, User)
                .outerjoin(User, User.id == StoreOversight.watcher_id)
                .where(
                    StoreOversight.target_username == user.username.lower(),
                    StoreOversight.status == OversightStatus.ACTIVE,
                )
            )
        ).all()

    if not rows:
        await message.answer("Вашу ленту действий никто не смотрит.")
        return

    for req, watcher in rows:
        name = "кто-то"
        if watcher is not None:
            name = watcher.first_name or (
                f"@{watcher.username}" if watcher.username else "кто-то"
            )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔒 Закрыть доступ", callback_data=f"{REVOKE}:{req.id}"
                    )
                ]
            ]
        )
        await message.answer(
            f"👀 <b>{name}</b> видит ленту действий вашего склада.\n"
            "Он не видит закупочные цены и прибыль и ничего не может менять.",
            reply_markup=kb,
        )

@dp.message(F.chat.type == "private", F.photo)
async def on_photo(message: Message):
    """Приём фото: отдаём file_id, который фронт сохранит в товар."""
    file_id = message.photo[-1].file_id
    await message.answer(f"Фото получено. file_id:\n`{file_id}`", parse_mode="Markdown")


async def set_menu_button():
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Склад", web_app=WebAppInfo(url=settings.webapp_url)
        )
    )


async def main():
    await set_menu_button()
    # Реакции Telegram не присылает без явной подписки на тип обновления.
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
