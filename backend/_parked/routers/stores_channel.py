"""Отложенное из роутера складов: настройки канала и шаблон скидок."""

def _normalize_channel(raw: str | None) -> str | None:
    """Приводит ввод к тому, что понимает Bot API: @username или числовой -100… id.
    Публичный: @name или t.me/name. Приватный: числовой id (@ нет)."""
    if not raw:
        return None
    v = raw.strip()
    if not v:
        return None
    # числовой id канала (-100…) — как есть
    if v.lstrip("-").isdigit():
        return v
    # публичная ссылка t.me/name -> @name (инвайт-ссылки t.me/+… НЕ трогаем)
    if "t.me/" in v:
        tail = v.split("t.me/", 1)[1].strip("/")
        if tail.startswith("+") or tail.startswith("joinchat"):
            return v  # приватная инвайт-ссылка — Bot API её не примет (тест подскажет)
        v = "@" + tail
    if not v.startswith("@"):
        v = "@" + v
    return v


@router.get("/channel", response_model=ChannelSettings)
async def get_channel(
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one()
    return ChannelSettings(
        channel_id=store.channel_id,
        channel_signature=store.channel_signature,
        watermark_enabled=store.watermark_enabled,
        watermark_text=store.watermark_text,
        bump_enabled=store.bump_enabled,
        bump_after_days=store.bump_after_days,
        preview_before_post=store.preview_before_post,
        subscriptions_enabled=store.subscriptions_enabled,
        auto_reply_enabled=store.auto_reply_enabled,
        discount_template=store.discount_template,
    )


@router.patch("/channel", response_model=ChannelSettings)
async def set_channel(
    payload: ChannelUpdate,
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    # Различаем «поле не прислали» и «прислали null»: без этого сохранение
    # одного лишь водяного знака выключало бы автопостинг целиком.
    touches_channel = "channel_id" in payload.model_fields_set
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one()
    channel = _normalize_channel(payload.channel_id) if touches_channel else store.channel_id
    if touches_channel:
        store.channel_id = channel
    if "channel_signature" in payload.model_fields_set:
        sig = (payload.channel_signature or "").strip()
        store.channel_signature = sig or None
    if payload.watermark_enabled is not None:
        store.watermark_enabled = payload.watermark_enabled
    if payload.watermark_text is not None:
        store.watermark_text = payload.watermark_text.strip() or None
    if payload.bump_enabled is not None:
        store.bump_enabled = payload.bump_enabled
    if payload.bump_after_days is not None:
        store.bump_after_days = payload.bump_after_days
    if payload.preview_before_post is not None:
        store.preview_before_post = payload.preview_before_post
    if payload.subscriptions_enabled is not None:
        store.subscriptions_enabled = payload.subscriptions_enabled
    if payload.auto_reply_enabled is not None:
        store.auto_reply_enabled = payload.auto_reply_enabled
    if payload.discount_template is not None:
        body = payload.discount_template.strip()
        if body:
            # Проверяем до сохранения: битый шаблон иначе ломал бы каждое
            # объявление о скидке, и понять это можно было бы только по
            # неотправленным заданиям.
            try:
                discounts_svc.validate_template(body)
            except PostTemplateError as e:
                raise HTTPException(422, str(e))
            store.discount_template = body
        else:
            store.discount_template = None  # пусто — вернуться к встроенному

    # Совместимость: постинг работает по таблице channels, а этот старый
    # эндпоинт правит поля склада. Держим их согласованными, иначе смена
    # канала в мини-аппе не влияла бы на публикацию.
    existing = (
        await session.execute(select(Channel).where(Channel.store_id == store.id))
    ).scalars().all() if touches_channel else []
    if touches_channel and channel:
        primary = next((c for c in existing if c.chat_id == channel), None)
        if primary is None:
            primary = Channel(store_id=store.id, chat_id=channel)
            session.add(primary)
        primary.enabled = True
        primary.signature = store.channel_signature
        # Прочие каналы, заведённые через старый экран, гасим: он одноканальный.
        for c in existing:
            if c.chat_id != channel:
                c.enabled = False
    elif touches_channel:
        for c in existing:
            c.enabled = False

    await session.commit()
    return ChannelSettings(
        channel_id=channel,
        channel_signature=store.channel_signature,
        watermark_enabled=store.watermark_enabled,
        watermark_text=store.watermark_text,
        bump_enabled=store.bump_enabled,
        bump_after_days=store.bump_after_days,
        preview_before_post=store.preview_before_post,
        subscriptions_enabled=store.subscriptions_enabled,
        auto_reply_enabled=store.auto_reply_enabled,
        discount_template=store.discount_template,
    )


@router.get("/discount-template", response_model=DiscountTemplateInfo)
async def get_discount_template(
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    """Текущий шаблон объявления о скидке с превью на примере."""
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one()
    body = store.discount_template or discounts_svc.DEFAULT_TEMPLATE
    return DiscountTemplateInfo(
        body=body,
        is_default=store.discount_template is None,
        preview=discounts_svc.render_demo(body),
        placeholders=[TemplatePlaceholder(**p) for p in discounts_svc.PLACEHOLDERS],
    )


@router.post("/channel/test")
async def test_channel(
    member: StoreMember = Depends(require_role(*OWNER_ONLY)),
    session: AsyncSession = Depends(get_session),
):
    store = (
        await session.execute(select(Store).where(Store.id == member.store_id))
    ).scalar_one()
    if not store.channel_id:
        raise HTTPException(422, "Канал не задан")
    try:
        await telegram_post.send_test(store.channel_id)
    except telegram_post.ChannelError as e:
        # Частые причины — бот не админ / неверный @username
        raise HTTPException(400, f"Не удалось отправить: {e}")
    return {"ok": True}
