"""Отложенные обработчики бота: канал, публикации, нейросеть.

Вырезаны из app/bot.py при сужении продукта до учёта вещей. Возврат —
вставить обратно и вернуть импорты отложенных сервисов.
"""

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


@dp.message(CommandStart(deep_link=True, magic=F.args.startswith("voice_")))
async def cmd_start_voice(message: Message, command: CommandObject):
    """Deep link из мини-аппа: ждём голосовое с описанием вещи."""
    token = (command.args or "")[len("voice_"):]
    session = await voice_capture.arm(token)
    if session is None or session["telegram_id"] != message.from_user.id:
        await message.answer(
            "Ссылка устарела. Вернитесь в приложение и нажмите «Надиктовать» ещё раз.",
            reply_markup=_webapp_kb(),
        )
        return
    await message.answer(
        "🎤 <b>Опишите вещь голосом</b>\n\n"
        "Запишите голосовое сообщение обычной кнопкой микрофона — одной фразой, "
        "как рассказали бы покупателю.\n\n"
        "Например: «взял найковскую олимпийку сорок шестого, отдал полтос, "
        "состояние отличное, продаю за сто пятьдесят».",
        parse_mode="HTML",
    )


@dp.message(F.chat.type == "private", F.voice | F.audio)
async def on_voice_note(message: Message):
    """Голосовое с описанием вещи — распознаём и раскладываем по полям."""
    active = await voice_capture.get_active_for_user(message.from_user.id)
    if active is None:
        raise SkipHandler
    token, session = active
    if session["status"] != voice_capture.ARMED:
        raise SkipHandler

    src = message.voice or message.audio
    note = await message.answer("⏳ Слушаю запись…")
    try:
        buf = BytesIO()
        await bot.download(src, destination=buf)
        data = buf.getvalue()
        mime = getattr(src, "mime_type", None) or "audio/ogg"
        result = await ai_voice.parse_voice_audio(data, mime)
    except ai_voice.VoiceAiUnavailable as e:
        await voice_capture.fail(token, str(e))
        await note.edit_text(f"❌ Не удалось разобрать запись: {e}")
        return
    except Exception as e:  # noqa: BLE001
        await voice_capture.fail(token, "внутренняя ошибка")
        logging.getLogger("voice").warning("voice note failed: %s", e)
        await note.edit_text("❌ Не удалось обработать запись. Попробуйте ещё раз.")
        return

    transcript = result.pop("transcript", "")
    await voice_capture.finish(token, result, transcript)

    filled = [
        f"{label}: <b>{post_template.esc(str(result[key]))}</b>"
        for key, label in (
            ("brand", "Бренд"), ("category", "Категория"), ("size", "Размер"),
            ("color", "Цвет"), ("condition", "Состояние"),
            ("cost_price", "Закупка"), ("list_price", "Цена продажи"),
        )
        if result.get(key) is not None
    ]
    body = "✅ <b>Услышал</b>\n\n"
    if transcript:
        body += f"<i>{post_template.esc(transcript)}</i>\n\n"
    body += "\n".join(filled) if filled else "Ничего разобрать не вышло."
    body += "\n\nВернитесь в приложение — поля уже заполнены."
    await note.edit_text(body, parse_mode="HTML", reply_markup=_webapp_kb())


@dp.callback_query(F.data.startswith(f"{preview.APPROVE}:") | F.data.startswith(f"{preview.DECLINE}:"))
async def on_preview_decision(cq: CallbackQuery):
    """Кнопки под предпросмотром: пускаем публикацию в работу или отменяем.

    Подтверждение НЕ публикует немедленно, а снимает блокировку: задание
    переходит из AWAITING в PENDING со своим прежним run_after. Пост,
    назначенный на вечер, так и уйдёт вечером.
    """
    parsed = preview.parse_callback(cq.data or "")
    if parsed is None:
        await cq.answer("Некорректная кнопка")
        return
    action, kind, entity_id = parsed

    # По какому полю и виду задания искать. Вид обязателен там, где ключ —
    # item_id: у вещи и её поднятия он один, и без фильтра одна кнопка
    # отпускала бы оба задания сразу.
    field, job_kind = {
        preview.ITEM: ("item_id", JobKind.POST_ITEM),
        preview.BUMP: ("item_id", JobKind.BUMP),
        preview.POST: ("custom_post_id", None),
        preview.DISCOUNT: ("discount_id", None),
        preview.DROP: ("drop_id", None),
    }[kind]

    async with SessionLocal() as s:
        user = (
            await s.execute(select(User).where(User.telegram_id == cq.from_user.id))
        ).scalar_one_or_none()
        if user is None:
            await cq.answer("Вы не зарегистрированы", show_alert=True)
            return

        q = select(PostJob).where(
            getattr(PostJob, field) == entity_id,
            PostJob.status == JobStatus.AWAITING,
        )
        if job_kind is not None:
            q = q.where(PostJob.kind == job_kind)
        jobs = (await s.execute(q)).scalars().all()
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

        approved = action == preview.APPROVE
        for job in jobs:
            job.status = JobStatus.PENDING if approved else JobStatus.CANCELLED

        extra = ""
        if not approved:
            extra = await _rollback_declined(s, kind, entity_id, user)

        scheduled_at = min((j.run_after for j in jobs), default=None)
        await s.commit()

    if approved:
        now = datetime.now(timezone.utc)
        note = (
            "✅ Отправляю в канал…"
            if scheduled_at is None or scheduled_at <= now
            else f"✅ Подтверждено. Выйдет {scheduled_at.astimezone().strftime('%d.%m в %H:%M')}"
        )
    else:
        note = "✖️ Публикация отменена" + extra

    await cq.answer(note[:200])
    with suppress(Exception):
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.message.reply(note)


async def _rollback_declined(s, kind: str, entity_id, user: User) -> str:
    """Приводит сущность в согласованное состояние после отказа.

    Без этого отменённая публикация оставляла бы следы: вещь висела бы в
    «Выставлен» без поста в канале, пост контент-плана ждал бы в
    расписании, а немедленная скидка так и осталась бы применённой к цене,
    хотя объявления о ней никто не увидит.
    """
    if kind == preview.ITEM:
        item = (
            await s.execute(select(Item).where(Item.id == entity_id))
        ).scalar_one_or_none()
        if item is not None and item.status == ItemStatus.LISTED:
            back = prev_status(ItemStatus.LISTED)
            if back is not None:
                repo = ItemRepository(s)
                if await repo.apply_status(
                    item, back, expected_version=item.version, changed_by=user.id
                ):
                    return ", вещь вернулась в «Сфотографирован»"
        return ""

    if kind == preview.POST:
        post = (
            await s.execute(select(CustomPost).where(CustomPost.id == entity_id))
        ).scalar_one_or_none()
        if post is not None and post.status == CustomPostStatus.SCHEDULED:
            post.status = CustomPostStatus.CANCELLED
            return ", пост снят с расписания"
        return ""

    if kind == preview.DISCOUNT:
        d = (
            await s.execute(select(Discount).where(Discount.id == entity_id))
        ).scalar_one_or_none()
        if d is None or d.status != DiscountStatus.SCHEDULED:
            return ""
        d.status = DiscountStatus.CANCELLED
        # Немедленную скидку цена вещи получает сразу при создании, ещё до
        # объявления. Отказ обязан вернуть прежний ценник, иначе вещь
        # молча продавалась бы дешевле без всякой акции.
        item = (
            await s.execute(select(Item).where(Item.id == d.item_id))
        ).scalar_one_or_none()
        if item is not None and item.list_price_orig == d.new_price:
            item.list_price_orig = d.old_price
            item.price_before_discount = None
            item.list_price = await fx.convert(
                d.old_price, d.currency, item.cost_currency or "BYN"
            )
            return ", цена возвращена"
        return ", скидка отменена"

    if kind == preview.DROP:
        return ", дроп не опубликован"

    if kind == preview.BUMP:
        # Отметка о поднятии, чтобы отклонённая вещь не предлагалась снова
        # на следующем же обходе очереди.
        await s.execute(
            update(Item)
            .where(Item.id == entity_id)
            .values(bumped_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )
        return ", вещь не поднята"
    return ""


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



REVOKE = "ovrm"


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


def _sample_html(message: Message) -> str:
    """Текст сообщения с сохранением форматирования (жирный, ссылки и т.д.)."""
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    if not text:
        return ""
    return html_decoration.unparse(text, entities)


# Только личка: в группе обсуждений это перехватывало бы комментарии.
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
