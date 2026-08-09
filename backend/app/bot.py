"""aiogram 3.x бот: WebApp-кнопка, /start (активация инвайтов), приём фото, CSV."""
from __future__ import annotations

import asyncio
import uuid
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
    Channel,
    Subscription,
    InviteStatus,
    Item,
    ItemPost,
    Role,
    JobKind,
    JobStatus,
    PostJob,
    PostTemplate,
    Store,
    StoreInvite,
    StoreMember,
    User,
)
from .services import (
    ai_template,
    comment_reply,
    post_template,
    preview,
    subscriptions,
    template_capture,
)
from .services.ai_describe import AiGenerationError, AiNotConfigured
from .services.fsm import SOLD_STATUSES

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


@dp.message(CommandStart(deep_link=True, magic=F.args.startswith("tpl_")))
async def cmd_start_template_capture(message: Message, command: CommandObject):
    """Deep link из мини-аппа: взводим сессию и ждём пример поста."""
    token = (command.args or "")[len("tpl_"):]
    session = await template_capture.arm(token)
    if session is None or session["telegram_id"] != message.from_user.id:
        await message.answer(
            "Ссылка устарела. Откройте раздел «Шаблоны постов» и нажмите "
            "«Скопировать дизайн из поста» ещё раз.",
            reply_markup=_webapp_kb(),
        )
        return
    await message.answer(
        "🎨 <b>Копирование дизайна</b>\n\n"
        "Пришлите пример поста, оформление которого вам нравится: "
        "перешлите его из канала или отправьте текстом.\n\n"
        "Я разберу структуру и соберу из неё шаблон — эмодзи, порядок строк "
        "и разметка сохранятся, а данные вещи станут подставляемыми полями.",
        parse_mode="HTML",
    )


def _sample_html(message: Message) -> str:
    """Текст сообщения с сохранением форматирования (жирный, ссылки и т.д.)."""
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    if not text:
        return ""
    return html_decoration.unparse(text, entities)


# Только личка: в группе обсуждений это перехватывало бы комментарии.
@dp.message(F.chat.type == "private", (F.text & ~F.text.startswith("/")) | F.caption)
async def on_template_sample(message: Message):
    """Пример поста для клонирования дизайна — только при активной сессии."""
    active = await template_capture.get_active_for_user(message.from_user.id)
    if active is None:
        raise SkipHandler  # обычное сообщение — пусть разбирают другие хендлеры
    token, session = active
    if session["status"] != template_capture.ARMED:
        raise SkipHandler

    sample = _sample_html(message)
    if not sample.strip():
        await message.answer("Не вижу текста в этом сообщении. Пришлите пост с текстом.")
        return

    note = await message.answer("⏳ Разбираю оформление…")
    try:
        result = await ai_template.clone_template_from_sample(sample)
    except AiNotConfigured:
        await template_capture.fail(token, "AI не настроен (нет GEMINI_API_KEY)")
        await note.edit_text("❌ AI-генерация не настроена: не задан GEMINI_API_KEY.")
        return
    except AiGenerationError as e:
        await template_capture.fail(token, str(e))
        await note.edit_text(f"❌ Не получилось разобрать пост: {e}")
        return

    async with SessionLocal() as s:
        store_id = uuid.UUID(session["store_id"])
        has_any = (
            await s.execute(
                select(PostTemplate.id).where(PostTemplate.store_id == store_id).limit(1)
            )
        ).scalar_one_or_none()
        tpl = PostTemplate(
            store_id=store_id,
            name=result["name"],
            body=result["body"],
            source_sample=sample[:4000],
            is_default=has_any is None,
        )
        s.add(tpl)
        await s.commit()
        await s.refresh(tpl)
        tpl_id, tpl_name = tpl.id, tpl.name

    await template_capture.finish(token, tpl_id)

    demo = post_template.render_demo(result["body"])
    await note.edit_text(
        f"✅ Шаблон «{post_template.esc(tpl_name)}» готов.\n\n"
        "Вот как он будет выглядеть на примере вещи:",
        parse_mode="HTML",
    )
    await message.answer(demo, parse_mode="HTML", reply_markup=_webapp_kb())


@dp.message(CommandStart(deep_link=True, magic=F.args.startswith("item_")))
async def cmd_start_item_card(message: Message, command: CommandObject):
    """Переход из канала: показываем карточку вещи и кнопку в мини-апп."""
    raw = (command.args or "")[len("item_"):]
    try:
        item_id = uuid.UUID(raw)
    except ValueError:
        await message.answer("Ссылка неверна.", reply_markup=_webapp_kb())
        return

    async with SessionLocal() as s:
        item = (
            await s.execute(select(Item).where(Item.id == item_id))
        ).scalar_one_or_none()
        if item is None or item.archived_at is not None:
            await message.answer("Вещь больше не доступна.", reply_markup=_webapp_kb())
            return
        store = (
            await s.execute(select(Store).where(Store.id == item.store_id))
        ).scalar_one_or_none()
        tpl = (
            await s.execute(
                select(PostTemplate.body).where(
                    PostTemplate.store_id == item.store_id,
                    PostTemplate.is_default.is_(True),
                )
            )
        ).scalar_one_or_none()
        # Показываем карточку любому — это витрина, финансов в ней нет.
        from .routers.items import _item_to_post_dict

        post = _item_to_post_dict(item)
        sold = item.status in SOLD_STATUSES
        caption = post_template.render(
            tpl or post_template.DEFAULT_TEMPLATE_BODY,
            post_template.build_context(
                post, store.channel_signature if store else None
            ),
        )
        photos = list(item.photo_file_ids or [])
        subs_on = bool(store and store.subscriptions_enabled)
        kb = _card_kb(item.id) if subs_on else _webapp_kb()

    if sold:
        caption = "✅ <b>ПРОДАНО</b>\n\n" + caption
    photo = photos[0] if photos else None
    if photo and photo.startswith("local:"):
        data = _read_local_photo(photo)
        if data is not None:
            await message.answer_photo(
                BufferedInputFile(data, filename="item.jpg"),
                caption=caption[:1024],
                parse_mode="HTML",
                reply_markup=kb,
            )
            return
    await message.answer(caption[:4096], parse_mode="HTML", reply_markup=kb)


def _card_kb(item_id) -> InlineKeyboardMarkup:
    """Карточка вещи: открыть склад + подписаться на похожее."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧥 Открыть склад",
                                  web_app=WebAppInfo(url=settings.webapp_url))],
            [InlineKeyboardButton(text="🔔 Ждать похожее",
                                  callback_data=f"sub:{item_id}")],
        ]
    )


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

        await session.commit()

    greeting = "Добро пожаловать в ресейл-ERP!"
    if activated:
        greeting = f"Вы добавлены на {activated} склад(ов) по приглашению."
    await message.answer(greeting, reply_markup=_webapp_kb())


def _owner_role():
    from .models import Role
    return Role.OWNER


@dp.callback_query(F.data.startswith(f"{preview.APPROVE}:") | F.data.startswith(f"{preview.DECLINE}:"))
async def on_preview_decision(cq: CallbackQuery):
    """Кнопки под предпросмотром: публикуем задания или отменяем их."""
    action, _, raw_id = (cq.data or "").partition(":")
    try:
        item_id = uuid.UUID(raw_id)
    except ValueError:
        await cq.answer("Некорректная кнопка")
        return

    async with SessionLocal() as s:
        user = (
            await s.execute(select(User).where(User.telegram_id == cq.from_user.id))
        ).scalar_one_or_none()
        if user is None:
            await cq.answer("Вы не зарегистрированы", show_alert=True)
            return

        jobs = (
            await s.execute(
                select(PostJob).where(
                    PostJob.item_id == item_id,
                    PostJob.kind == JobKind.POST_ITEM,
                    PostJob.status == JobStatus.AWAITING,
                )
            )
        ).scalars().all()
        if not jobs:
            await cq.answer("Этот предпросмотр уже неактуален")
            with suppress(Exception):
                await cq.message.edit_reply_markup(reply_markup=None)
            return

        # Кнопку мог нажать кто угодно, кому переслали сообщение, — проверяем,
        # что человек действительно вправе публиковать на этом складе.
        allowed = (
            await s.execute(
                select(StoreMember).where(
                    StoreMember.user_id == user.id,
                    StoreMember.store_id == jobs[0].store_id,
                    StoreMember.role.in_((Role.OWNER, Role.EMPLOYEE)),
                )
            )
        ).scalar_one_or_none()
        if allowed is None:
            await cq.answer("Нет прав на публикацию", show_alert=True)
            return

        new_status = JobStatus.PENDING if action == preview.APPROVE else JobStatus.CANCELLED
        for job in jobs:
            job.status = new_status
        await s.commit()

    note = ("✅ Отправляю в канал…" if action == preview.APPROVE
            else "✖️ Публикация отменена")
    await cq.answer(note)
    with suppress(Exception):
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.message.reply(note)


@dp.callback_query(F.data.startswith("sub:"))
async def on_subscribe(cq: CallbackQuery):
    """Кнопка «Ждать похожее»: подписка по бренду и размеру этой вещи."""
    try:
        item_id = uuid.UUID((cq.data or "")[4:])
    except ValueError:
        await cq.answer("Некорректная кнопка")
        return
    async with SessionLocal() as s:
        item = (await s.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()
        if item is None:
            await cq.answer("Вещь больше не доступна", show_alert=True)
            return
        store = (await s.execute(select(Store).where(Store.id == item.store_id))).scalar_one()
        if not store.subscriptions_enabled:
            await cq.answer("Подписки сейчас отключены", show_alert=True)
            return
        # Повторное нажатие не плодит дубли — оживляем прежнюю подписку.
        existing = (
            await s.execute(
                select(Subscription).where(
                    Subscription.store_id == store.id,
                    Subscription.telegram_id == cq.from_user.id,
                    Subscription.brand == item.brand,
                    Subscription.size == item.size,
                )
            )
        ).scalars().first()
        if existing is not None:
            if existing.active:
                await cq.answer("Вы уже подписаны на такие вещи")
                return
            existing.active = True
            sub = existing
        else:
            sub = Subscription(
                store_id=store.id,
                telegram_id=cq.from_user.id,
                username=cq.from_user.username,
                brand=item.brand,
                size=item.size,
            )
            s.add(sub)
        await s.commit()
        text = subscriptions.describe(sub)
    await cq.answer("Подписка оформлена")
    await cq.message.answer(
        f"🔔 Буду сообщать, когда появится: <b>{post_template.esc(text)}</b>\n\n"
        "Список подписок — команда /подписки",
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith(f"{preview.UNSUB}:"))
async def on_unsubscribe(cq: CallbackQuery):
    """Отписка — кнопка под уведомлением или в списке подписок."""
    try:
        sub_id = uuid.UUID((cq.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        await cq.answer("Некорректная кнопка")
        return
    async with SessionLocal() as s:
        sub = (await s.execute(select(Subscription).where(Subscription.id == sub_id))).scalar_one_or_none()
        if sub is None or sub.telegram_id != cq.from_user.id:
            await cq.answer("Подписка не найдена")
            return
        sub.active = False
        await s.commit()
    await cq.answer("Отписал")
    with suppress(Exception):
        await cq.message.edit_reply_markup(reply_markup=None)


@dp.message(Command("подписки", "subs", "subscriptions"))
async def cmd_subs(message: Message):
    """Список активных подписок с кнопками отписки."""
    async with SessionLocal() as s:
        subs = (
            await s.execute(
                select(Subscription).where(
                    Subscription.telegram_id == message.from_user.id,
                    Subscription.active.is_(True),
                )
            )
        ).scalars().all()
        rows = [(x.id, subscriptions.describe(x)) for x in subs]
    if not rows:
        await message.answer(
            "У вас нет подписок. Откройте карточку вещи из канала и нажмите "
            "«Ждать похожее»."
        )
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🔕 {t}", callback_data=f"{preview.UNSUB}:{i}")]
            for i, t in rows
        ]
    )
    await message.answer("Ваши подписки — нажмите, чтобы отписаться:", reply_markup=kb)


@dp.message(F.is_automatic_forward)
async def on_channel_post_forwarded(message: Message):
    """Пост автоматически переслан в группу обсуждений — запоминаем ветку.

    Именно на этой копии висят комментарии, и её message_id становится
    message_thread_id всех ответов. Без этой привязки связать вопрос
    с вещью невозможно.
    """
    origin_chat = message.forward_from_chat
    origin_id = message.forward_from_message_id
    if origin_chat is None or origin_id is None:
        raise SkipHandler
    chat_id = str(origin_chat.id)
    uname = f"@{origin_chat.username}" if origin_chat.username else None
    async with SessionLocal() as s:
        conds = [Channel.chat_id == chat_id]
        if uname:
            conds.append(Channel.chat_id == uname)
        ch = (await s.execute(select(Channel).where(or_(*conds)))).scalars().first()
        if ch is None:
            raise SkipHandler
        await s.execute(
            update(ItemPost)
            .where(ItemPost.channel_id == ch.id, ItemPost.message_id == origin_id)
            .values(
                discussion_chat_id=message.chat.id,
                discussion_message_id=message.message_id,
            )
            .execution_options(synchronize_session=False)
        )
        await s.commit()
    raise SkipHandler  # не мешаем другим обработчикам


@dp.message(F.message_thread_id & F.text & ~F.text.startswith("/"))
async def on_comment(message: Message):
    """Вопрос в комментариях под постом — отвечаем, если знаем ответ."""
    async with SessionLocal() as s:
        row = (
            await s.execute(
                select(ItemPost, Item, Store)
                .join(Item, Item.id == ItemPost.item_id)
                .join(Store, Store.id == Item.store_id)
                .where(
                    ItemPost.discussion_chat_id == message.chat.id,
                    ItemPost.discussion_message_id == message.message_thread_id,
                )
            )
        ).first()
        if row is None:
            raise SkipHandler
        _post, item, store = row
        if not store.auto_reply_enabled:
            raise SkipHandler
        from .routers.items import _item_to_post_dict

        data = _item_to_post_dict(item)
        data["status"] = item.status.value
        data["currency"] = item.price_currency

    reply = comment_reply.answer(data, message.text or "")
    if reply is None:
        raise SkipHandler  # не поняли вопрос — молчим, а не отвечаем невпопад
    with suppress(Exception):
        await message.reply(reply)


@dp.message_reaction_count()
async def on_reactions(event: MessageReactionCountUpdated):
    """Счётчик реакций на пост канала — записываем в карточку публикации."""
    total = sum(r.total_count for r in (event.reactions or []))
    chat_id = str(event.chat.id)
    username = f"@{event.chat.username}" if event.chat.username else None
    async with SessionLocal() as s:
        # Канал мог быть заведён и по числовому id, и по @username.
        conds = [Channel.chat_id == chat_id]
        if username:
            conds.append(Channel.chat_id == username)
        ch = (
            await s.execute(select(Channel).where(or_(*conds)))
        ).scalars().first()
        if ch is None:
            return
        await s.execute(
            update(ItemPost)
            .where(ItemPost.channel_id == ch.id, ItemPost.message_id == event.message_id)
            .values(reactions=total)
            .execution_options(synchronize_session=False)
        )
        await s.commit()


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


# Только личка: иначе бот отвечал бы file_id на каждое фото в группе.
@dp.message(F.chat.type == "private", F.photo)
async def on_photo(message: Message):
    """Приём фото: отдаём file_id, который фронт сохранит в товар."""
    # Ждём пример поста, а пришло фото без подписи — подсказываем, а не молчим.
    active = await template_capture.get_active_for_user(message.from_user.id)
    if active is not None and active[1]["status"] == template_capture.ARMED:
        await message.answer(
            "В этом сообщении нет текста — копировать нечего. "
            "Перешлите пост вместе с подписью или пришлите текст поста."
        )
        return
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
