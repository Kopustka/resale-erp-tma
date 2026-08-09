# Resale ERP TMA — Backend

Telegram Mini App для ресейла брендовой/архивной одежды: складской учёт, статусы товаров (FSM), BI-аналитика, Scout Mode.

## Стек
FastAPI · SQLAlchemy 2.0 (async, asyncpg) · PostgreSQL · Redis · aiogram 3.x · Pydantic v2

## Ключевые решения
- **Одна валюта** (рубли), деньги — `Numeric`, без конвертаций.
- **Роль привязана к паре `user↔store`** (таблица `store_members`), а не к юзеру — один человек может быть OWNER на своём складе и EMPLOYEE на чужом.
- **`net_profit` / `roi_percent` не хранятся** — `hybrid_property` (вычисление в Python + SQL-выражение), нет дрейфа данных.
- **Свайпы**: `Idempotency-Key` (Redis) + optimistic lock по `version` → защита от двойного перехода и гонок.
- **Фото**: хранится Telegram `file_id`, отдаётся через прокси `/api/v1/media/{item}/{index}` (file_id нельзя рендерить в `<img>` напрямую).
- **initData**: проверка HMAC-SHA256 + `auth_date` TTL (anti-replay) + `compare_digest`.
- **Soft-delete** (`archived_at`), cursor-пагинация, аудит статусов (`item_status_logs`).

## Структура
```
app/
  config.py        настройки (env)
  db.py            async engine + session
  models.py        SQLAlchemy 2.0 модели + FSM/Role enums
  schemas.py       Pydantic v2
  auth.py          валидация initData, get_current_user, require_role
  services/        fsm (матрица переходов), scout_parser, idempotency
  repositories/    items (cursor, optimistic lock), analytics
  routers/         items, scout, analytics, stores, media
  bot.py           aiogram: WebApp-кнопка, /start (активация инвайтов), приём фото
```

## Запуск (Docker)
```bash
cp .env.example .env      # впишите BOT_TOKEN и WEBAPP_URL
docker compose up --build
```
API: http://localhost:8000 · Swagger: http://localhost:8000/docs

## Запуск (локально)
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# поднимите postgres+redis, задайте DATABASE_URL/REDIS_URL/BOT_TOKEN в .env
uvicorn app.main:app --reload      # API
python -m app.bot                  # бот (отдельный процесс)
```
Таблицы создаются автоматически при старте (для прода — Alembic).

## Тесты
```bash
python smoke_test.py   # доменная логика на реальном Postgres (FSM, lock, аналитика, Scout)
python api_test.py     # HTTP-слой: подпись initData, роутеры, идемпотентность, роли
```

## API (кратко)
| Метод | Путь | Роль | Назначение |
|---|---|---|---|
| GET | `/api/v1/stores` | любая | склады юзера + роль |
| POST | `/api/v1/stores/switch` | любая | сменить активный склад |
| GET | `/api/v1/stores/members` | OWNER | участники склада |
| POST | `/api/v1/stores/invites` | OWNER | пригласить по `@username` |
| GET | `/api/v1/items` | любая | список (cursor, фильтры, `ids` для drill-down) |
| POST | `/api/v1/items` | OWNER/EMPLOYEE | создать товар |
| PATCH | `/api/v1/items/{id}/status` | OWNER/EMPLOYEE | свайп статуса (Idempotency-Key + version) |
| DELETE | `/api/v1/items/{id}` | OWNER/EMPLOYEE | архивация (soft-delete) |
| GET | `/api/v1/items/suggest/{brand\|category}` | любая | автодополнение |
| POST | `/api/v1/scout/analyze` | любая | разбор голоса + вердикт |
| GET | `/api/v1/analytics/summary` | OWNER/ANALYST | BI-сводка |
| GET | `/api/v1/media/{id}/{i}` | любая | фото-прокси |

Все запросы требуют заголовок `X-TG-Init-Data`. PATCH статуса — плюс `Idempotency-Key`.
