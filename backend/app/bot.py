"""aiogram 3.x бот: WebApp-кнопка, /start (активация инвайтов), приём фото, CSV."""
from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal
from .models import (
    InviteStatus,
    Store,
    StoreInvite,
    StoreMember,
    User,
)

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

        await session.commit()

    greeting = "Добро пожаловать в ресейл-ERP!"
    if activated:
        greeting = f"Вы добавлены на {activated} склад(ов) по приглашению."
    await message.answer(greeting, reply_markup=_webapp_kb())


def _owner_role():
    from .models import Role
    return Role.OWNER


@dp.my_chat_member()
async def on_added_to_chat(event: ChatMemberUpdated):
    """Бота добавили/сделали админом в канале — сообщаем тому, кто добавил,
    ID канала (нужен для приватных каналов, где нет @username)."""
    chat = event.chat
    if chat.type not in ("channel", "supergroup"):
        return
    status = event.new_chat_member.status
    if status not in ("administrator", "member"):
        return
    actor = event.from_user
    if actor is None:
        return
    title = chat.title or "канал"
    handle = f"@{chat.username}" if chat.username else None
    ident = handle or str(chat.id)
    lines = [
        f"✅ Меня добавили в «{title}».",
        "",
        "Чтобы сюда постились выставленные вещи, вставьте это в",
        "Настройки → Автопостинг в Telegram-канал:",
        "",
        f"`{ident}`",
    ]
    if handle:
        lines += ["", f"(публичный канал — можно и просто {handle})"]
    else:
        lines += ["", "Это приватный канал — используйте именно числовой ID выше."]
    try:
        await bot.send_message(actor.id, "\n".join(lines), parse_mode="Markdown")
    except Exception:
        pass


@dp.message(F.photo)
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
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
