"""Эффективность каналов — отложено вместе с автопостингом.

Запрос опирался на таблицы channels и item_posts. Возврат — вернуть метод
в app/repositories/analytics.py, поле by_channel в схему AnalyticsSummary
и секцию «Каналы» в BiScreen.vue (см. _parked/BiScreen_channels.txt).
"""

    async def by_channel(self, store_id: uuid.UUID) -> list[dict]:
        """Эффективность каналов: выложено, продано, конверсия, срок, прибыль.

        Честная оговорка о цифрах: вещь может висеть сразу в нескольких
        каналах, и по данным нельзя определить, какой из них привёл
        покупателя. Поэтому продажа засчитывается КАЖДОМУ каналу, где вещь
        публиковалась, и сумма прибыли по каналам может превышать общую.
        Это метрика для сравнения каналов между собой, а не разбиение выручки.
        """
        invested = Item.cost_price + Item.restore_cost + Item.delivery_cost
        profit = Item.selling_price - invested - Item.platform_fee
        is_sold = and_(Item.status.in_(SOLD_STATUSES), Item.selling_price.isnot(None))
        days = func.extract("epoch", Item.sold_date - ItemPost.created_at) / 86400.0

        rows = (
            await self.session.execute(
                select(
                    Channel.id.label("channel_id"),
                    Channel.chat_id,
                    Channel.title,
                    Channel.enabled,
                    # Считаем по Item, а не по ItemPost: фильтр архива стоит
                    # в условии соединения, и у архивной вещи Item уходит в NULL,
                    # тогда как строка поста осталась бы посчитанной.
                    func.count(Item.id).label("posted"),
                    func.count(case((is_sold, ItemPost.id))).label("sold"),
                    func.coalesce(
                        func.sum(case((is_sold, profit), else_=0)), 0
                    ).label("profit"),
                    func.avg(
                        case((and_(is_sold, Item.sold_date.isnot(None)), days))
                    ).label("avg_days"),
                    func.coalesce(func.sum(ItemPost.reactions), 0).label("reactions"),
                )
                .select_from(Channel)
                .outerjoin(ItemPost, ItemPost.channel_id == Channel.id)
                .outerjoin(
                    Item,
                    and_(Item.id == ItemPost.item_id, Item.archived_at.is_(None)),
                )
                .where(Channel.store_id == store_id)
                .group_by(Channel.id, Channel.chat_id, Channel.title, Channel.enabled,
                          Channel.created_at)
                .order_by(Channel.created_at)
            )
        ).all()

        out = []
        for r in rows:
            posted = r.posted or 0
            sold = r.sold or 0
            out.append(
                {
                    "channel_id": r.channel_id,
                    "chat_id": r.chat_id,
                    "title": r.title,
                    "enabled": r.enabled,
                    "posted": posted,
                    "sold": sold,
                    "sell_through": (sold / posted * 100) if posted else None,
                    "avg_days": float(r.avg_days) if r.avg_days is not None else None,
                    "profit": r.profit or Decimal(0),
                    "reactions": r.reactions or 0,
                }
            )
        return out

