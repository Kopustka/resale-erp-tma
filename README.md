# Resale ERP — Telegram Mini App

Закрытая ERP/складская система + BI-аналитика для бизнеса по ресейлу брендовой и архивной одежды (секонд-хенд). Реализация по ТЗ из `../промт для менеджера продаж одежды.txt`.

## Что это
- **Склад**: поштучный учёт уникальных вещей, статусы-FSM, инстант-свайпы (оптимистичный UI).
- **Scout Mode**: голосовой помощник на закупке — по фразе «худи найк сорок» выдаёт исторический вердикт БРАТЬ/НЕ БРАТЬ.
- **BI-аналитика**: зависшие товары, ROI по точкам закупки, оборачиваемость (drill-down в склад).
- **Роли и мультиаккаунтинг**: OWNER / EMPLOYEE / ANALYST, роль привязана к складу; приглашение по `@username`.

## Структура
```
app/
  backend/   FastAPI + SQLAlchemy 2.0 + PostgreSQL + Redis + aiogram   (см. backend/README.md)
  frontend/  Vue 3 + TypeScript + Pinia + Vite (TMA)                    (см. frontend/README.md)
```

## Быстрый старт
1. **Backend**: `cd backend && cp .env.example .env` (впишите `BOT_TOKEN`), затем `docker compose up --build`.
2. **Frontend**: `cd frontend && npm install && npm run dev`. Для разработки вне Telegram задайте `VITE_DEV_INIT_DATA` и `VITE_API_URL`.
3. Пропишите бота в @BotFather, укажите `WEBAPP_URL` (HTTPS-туннель на фронтенд) — кнопка меню откроет Mini App.

## Статус реализации
- ✅ Backend: модели, FSM, авторизация initData, репозитории, роутеры, бот, media-прокси.
- ✅ Тесты: `smoke_test.py` (доменная логика) и `api_test.py` (HTTP-слой) — проходят на реальном Postgres+Redis.
- ✅ Frontend: 5 экранов TMA (Склад, Создание, Scout, BI, Настройки).

## Дальнейшие шаги (вне MVP)
- Alembic-миграции вместо `create_all`.
- Реальный эндпоинт экспорта CSV через бота.
- Загрузка фото из формы создания (сейчас file_id приходит через бота).
- Кэш тяжёлых BI-агрегатов в Redis с инвалидацией.
