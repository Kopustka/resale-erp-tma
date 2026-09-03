"""Отложенное из роутера вещей: публикация в канал и нейросеть.

Вырезано при сужении продукта до учёта. Возврат — вернуть функции в
app/routers/items.py и восстановить вызовы в patch_status/create_item.
"""

@router.post("/parse-voice", response_model=VoiceParseResult)
async def parse_voice(
    payload: VoiceParseRequest,
    member: StoreMember = Depends(get_active_membership),
):
    """Разбор голосовой фразы в поля новой вещи (для предзаполнения формы)."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot create items")

    # Основной путь — модель: она понимает живую речь, а не список слов.
    try:
        data = await ai_voice.parse_voice_ai(payload.text)
        return VoiceParseResult(**data)
    except ai_voice.VoiceAiUnavailable as e:
        logging.getLogger("ai_voice").warning("откат на словарный разбор: %s", e)

    # Запасной путь: без ключа, при исчерпанной квоте или сбое сети функция
    # обязана продолжать работать, пусть и хуже.
    p = parse_item_voice(payload.text)
    return VoiceParseResult(
        brand=p.brand,
        category=p.category,
        size=p.size,
        color=p.color,
        condition=p.condition,
        cost_price=p.cost_price,
        title=p.title,
        low_confidence=True,  # словарь разбирает грубее — просим проверить
    )


MAX_VOICE_MB = 10


@router.post("/voice-upload", response_model=VoiceParseResult)
async def voice_upload(
    file: UploadFile = File(...),
    member: StoreMember = Depends(get_active_membership),
):
    """Запись из мини-аппа: распознаём и раскладываем по полям.

    Браузер отдаёт webm или mp4 в зависимости от платформы, поэтому всё
    приводим к ogg — модель документированно понимает именно его.
    """
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot create items")

    data = await file.read()
    if not data:
        raise HTTPException(422, "Пустая запись")
    if len(data) > MAX_VOICE_MB * 1024 * 1024:
        raise HTTPException(413, f"Запись больше {MAX_VOICE_MB} МБ")

    try:
        ogg = await audio.to_ogg(data)
    except audio.AudioError as e:
        logging.getLogger("ai_voice").warning("перекодирование не удалось: %s", e)
        raise HTTPException(422, "Не удалось прочитать запись — попробуйте ещё раз")

    try:
        result = await ai_voice.parse_voice_audio(ogg, "audio/ogg")
    except ai_voice.VoiceAiUnavailable as e:
        # Уровень warning, а не info: без него причина 503 не видна в журнале,
        # и разбираться приходится вслепую.
        logging.getLogger("ai_voice").warning("разбор записи не удался: %s", e)
        detail = (
            "Исчерпан дневной лимит распознавания — обновите ключ Gemini"
            if "квота" in str(e).lower()
            else "Распознавание временно недоступно, попробуйте ещё раз"
        )
        raise HTTPException(503, detail)

    transcript = result.get("transcript") or ""
    # Ничего не распознали — честно говорим об этом, а не отдаём пустую форму.
    if not transcript:
        raise HTTPException(422, "Речь не распознана — запишите ещё раз, ближе к микрофону")
    return VoiceParseResult(**result)


@router.post("/voice-capture", response_model=VoiceCaptureOut)
async def start_voice_capture(
    user: User = Depends(get_current_user),
    member: StoreMember = Depends(get_active_membership),
):
    """Готовит ссылку в чат с ботом, куда надиктовать голосовое."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot create items")
    username = await telegram_post.get_bot_username()
    if not username:
        raise HTTPException(503, "Не удалось определить бота")
    token = await voice_capture.create(user.telegram_id)
    return VoiceCaptureOut(
        token=token,
        deep_link=f"https://t.me/{username}?start=voice_{token}",
        expires_in=voice_capture.TTL,
    )


@router.get("/voice-capture/{token}", response_model=VoiceCaptureStatus)
async def voice_capture_status(
    token: str,
    user: User = Depends(get_current_user),
    _: StoreMember = Depends(get_active_membership),
):
    data = await voice_capture.get(token)
    if data is None:
        return VoiceCaptureStatus(status="expired")
    if data["telegram_id"] != user.telegram_id:
        raise HTTPException(404, "Session not found")
    fields = data.get("fields")
    return VoiceCaptureStatus(
        status=data["status"],
        fields=VoiceParseResult(**fields) if fields else None,
        transcript=data.get("transcript"),
        error=data.get("error"),
    )


@router.delete("/voice-capture/{token}", status_code=204)
async def cancel_voice_capture(
    token: str,
    user: User = Depends(get_current_user),
    _: StoreMember = Depends(get_active_membership),
):
    data = await voice_capture.get(token)
    if data is not None and data["telegram_id"] == user.telegram_id:
        await voice_capture.cancel(token)


async def _bg_generate_description(
    item_id: uuid.UUID,
    photos: list[str],
    fields: dict,
    need_title: bool,
    need_descr: bool,
) -> None:
    """Фоновая AI-генерация после создания вещи. Ошибки только логируем —
    вещь уже сохранена, перегенерировать можно из карточки."""
    try:
        gen = await generate_item_description(photos, fields)
    except (AiNotConfigured, AiGenerationError) as e:
        logging.getLogger("ai").warning("bg-generate %s failed: %s", item_id, e)
        return
    values: dict = {}
    if need_title:
        values["title"] = gen["title"]
    if need_descr:
        values["description"] = gen["description"]
    if not values:
        return
    # Версию НЕ трогаем: правка текстовых полей не должна ломать
    # optimistic lock параллельного перехода.
    async with SessionLocal() as s:
        await s.execute(
            update(Item)
            .where(Item.id == item_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await s.commit()


@router.post("/{item_id}/ai-describe", response_model=AiDescribeOut)
async def ai_describe(
    item_id: uuid.UUID,
    member: StoreMember = Depends(get_active_membership),
    session: AsyncSession = Depends(get_session),
):
    """Перегенерация названия/описания по фото вещи. НЕ сохраняет — фронт
    подставляет результат в форму, юзер правит и сохраняет сам."""
    if member.role not in CAN_EDIT:
        raise HTTPException(403, "Role cannot use AI describe")
    repo = ItemRepository(session)
    item = await repo.get(member.store_id, item_id, include_archived=True)
    if item is None:
        raise HTTPException(404, "Item not found")
    if not item.photo_file_ids:
        raise HTTPException(422, "У вещи нет фото — добавьте хотя бы одно")
    try:
        gen = await generate_item_description(
            item.photo_file_ids,
            {
                "brand": item.brand,
                "category": item.category,
                "size": item.size,
                "color": item.color,
                "condition": item.condition,
            },
        )
    except AiNotConfigured:
        raise HTTPException(503, "AI не настроен: добавьте GEMINI_API_KEY на сервере")
    except AiGenerationError as e:
        raise HTTPException(502, str(e))
    return AiDescribeOut(**gen)


def _item_to_post_dict(it: Item) -> dict:
    # Цена в объявлении: list_price, иначе фактическая цена продажи (в валюте продажи).
    price = it.list_price_orig if it.list_price_orig is not None else it.selling_price_orig
    return {
        "id": it.id,
        "title": it.title,
        "description": it.description,
        "brand": it.brand,
        "category": it.category,
        "size": it.size,
        "color": it.color,
        "condition": it.condition,
        "sku": it.sku,
        "length_cm": it.length_cm,
        "width_cm": it.width_cm,
        "sleeve_cm": it.sleeve_cm,
        "price": price,
        "price_currency": it.price_currency,
        "photo_file_ids": list(it.photo_file_ids or []),
    }


async def _enqueue_publish(
    session, store_id: uuid.UUID, item_id: uuid.UUID, actor: User | None = None
) -> None:
    """Ставит публикацию во все включённые каналы склада, кроме уже опубликованных.

    Если у склада включён предпросмотр, задания создаются в статусе AWAITING
    и уходят в личку на подтверждение — воркер их не тронет.
    """
    channels = (
        await session.execute(
            select(Channel).where(Channel.store_id == store_id, Channel.enabled.is_(True))
        )
    ).scalars().all()
    if not channels:
        return
    posted = set(
        (
            await session.execute(
                select(ItemPost.channel_id).where(ItemPost.item_id == item_id)
            )
        ).scalars().all()
    )
    queued = set(
        (
            await session.execute(
                select(PostJob.channel_uid).where(
                    PostJob.item_id == item_id,
                    PostJob.kind == JobKind.POST_ITEM,
                    PostJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                )
            )
        ).scalars().all()
    )
    store = (
        await session.execute(select(Store).where(Store.id == store_id))
    ).scalar_one_or_none()
    need_preview = bool(store and store.preview_before_post and actor is not None)

    targets = [ch for ch in channels if ch.id not in posted and ch.id not in queued]
    if not targets:
        return
    for ch in targets:
        job = await post_queue.enqueue(
            session,
            store_id=store_id,
            kind=JobKind.POST_ITEM,
            channel_id=ch.chat_id,
            channel_uid=ch.id,
            item_id=item_id,
        )
        if need_preview:
            job.status = JobStatus.AWAITING
    await session.commit()

    if not need_preview:
        return

    item = (
        await session.execute(select(Item).where(Item.id == item_id))
    ).scalar_one_or_none()
    if item is None:
        return
    caption = telegram_post.build_caption(
        _item_to_post_dict(item),
        targets[0].signature or (store.channel_signature if store else None),
        await _default_template_body(session, store_id),
    )
    photos = list(item.photo_file_ids or [])
    # Отправка уходит в фон: внутри запроса она грузила фото в Telegram и
    # интерфейс залипал на несколько секунд.
    asyncio.create_task(
        _bg_send_preview(
            actor.telegram_id,
            item_id,
            caption,
            photos[0] if photos else None,
            len(targets),
            await _watermark_text(session, store_id),
        )
    )


async def _bg_send_preview(
    telegram_id: int,
    item_id: uuid.UUID,
    caption: str,
    photo: str | None,
    channels: int,
    watermark: str | None,
) -> None:
    """Шлёт предпросмотр в личку вне HTTP-запроса.

    Если доставить не удалось (бот заблокирован, диалог не начат) — снимаем
    ожидание и публикуем как обычно: лучше пост без подтверждения, чем вещь,
    зависшая в AWAITING навсегда.
    """
    try:
        sent = await preview.send_preview(
            telegram_id, item_id, caption, photo, channels, watermark
        )
    except Exception as e:  # noqa: BLE001
        logging.getLogger("preview").warning("preview task failed: %s", e)
        sent = False
    if sent:
        return
    async with SessionLocal() as s:
        await s.execute(
            update(PostJob)
            .where(
                PostJob.item_id == item_id,
                PostJob.kind == JobKind.POST_ITEM,
                PostJob.status == JobStatus.AWAITING,
            )
            .values(status=JobStatus.PENDING)
            .execution_options(synchronize_session=False)
        )
        await s.commit()


async def _enqueue_unpublish(session, store_id: uuid.UUID, item_id: uuid.UUID) -> None:
    """Снимает вещь с публикации во всех каналах, где она висит."""
    rows = (
        await session.execute(
            select(ItemPost, Channel)
            .join(Channel, Channel.id == ItemPost.channel_id)
            .where(ItemPost.item_id == item_id)
        )
    ).all()
    if not rows:
        return
    queued = set(
        (
            await session.execute(
                select(PostJob.channel_uid).where(
                    PostJob.item_id == item_id,
                    PostJob.kind == JobKind.UNPUBLISH,
                    PostJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                )
            )
        ).scalars().all()
    )
    for post, ch in rows:
        if ch.id in queued:
            continue
        await post_queue.enqueue(
            session,
            store_id=store_id,
            kind=JobKind.UNPUBLISH,
            channel_id=ch.chat_id,
            channel_uid=ch.id,
            item_id=item_id,
            message_id=post.message_id,
        )
    await session.commit()


async def _enqueue_price_edit(
    session, store_id: uuid.UUID, item_id: uuid.UUID, before, after
) -> None:
    """Перерисовывает подпись во всех каналах, где вещь ещё висит непроданной."""
    rows = (
        await session.execute(
            select(ItemPost, Channel)
            .join(Channel, Channel.id == ItemPost.channel_id)
            .where(ItemPost.item_id == item_id, ItemPost.sold_marked.is_(False))
        )
    ).all()
    if not rows:
        return
    # Цену снизили — вешаем плашку скидки, это заметно поднимает отклик.
    prefix = None
    if before is not None and after is not None and after < before:
        pct = int(round((1 - float(after) / float(before)) * 100))
        prefix = f"🔥 <b>СКИДКА −{pct}%</b>\n\n" if pct >= 1 else None
    for post, ch in rows:
        await post_queue.enqueue(
            session,
            store_id=store_id,
            kind=JobKind.EDIT_CAPTION,
            channel_id=ch.chat_id,
            channel_uid=ch.id,
            item_id=item_id,
            message_id=post.message_id,
            caption_prefix=prefix,
        )
    await session.commit()


async def _enqueue_mark_sold(session, store_id: uuid.UUID, item_id: uuid.UUID) -> None:
    """Помечает проданными все посты вещи во всех каналах, где она висит."""
    rows = (
        await session.execute(
            select(ItemPost, Channel)
            .join(Channel, Channel.id == ItemPost.channel_id)
            .where(ItemPost.item_id == item_id, ItemPost.sold_marked.is_(False))
        )
    ).all()
    if not rows:
        return
    queued = set(
        (
            await session.execute(
                select(PostJob.channel_uid).where(
                    PostJob.item_id == item_id,
                    PostJob.kind == JobKind.MARK_SOLD,
                    PostJob.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
                )
            )
        ).scalars().all()
    )
    added = False
    for post, ch in rows:
        if ch.id in queued:
            continue
        await post_queue.enqueue(
            session,
            store_id=store_id,
            kind=JobKind.MARK_SOLD,
            channel_id=ch.chat_id,
            channel_uid=ch.id,
            item_id=item_id,
            message_id=post.message_id,
        )
        added = True
    if added:
        await session.commit()


async def _watermark_text(session, store_id: uuid.UUID) -> str | None:
    """Текст водяного знака: своё поле, иначе подпись канала. None — знак выключен."""
    row = (
        await session.execute(
            select(Store.watermark_enabled, Store.watermark_text, Store.channel_signature)
            .where(Store.id == store_id)
        )
    ).first()
    if row is None or not row[0]:
        return None
    return (row[1] or row[2] or "").strip() or None


async def _default_template_body(session, store_id: uuid.UUID) -> str | None:
    """Тело активного шаблона склада. None — встроенное оформление."""
    return (
        await session.execute(
            select(PostTemplate.body).where(
                PostTemplate.store_id == store_id, PostTemplate.is_default.is_(True)
            )
        )
    ).scalar_one_or_none()
