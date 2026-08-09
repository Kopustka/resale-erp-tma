# Resale ERP — Telegram Mini App (frontend)

Фронтенд TMA для учёта продажи б/у брендовой одежды (ресейл-ERP).
Vue 3 (`<script setup>`, Composition API) + TypeScript (strict) + Pinia + Vite.
Плоский минимализм, цвета — только из CSS-переменных темы Telegram, светлая/тёмная тема автоматически.

## Запуск

```bash
npm install
npm run dev
```

Открой `http://localhost:5173`.

Прод-сборка:

```bash
npm run build      # vue-tsc --noEmit + vite build
npm run preview
```

## Конфигурация (`.env`)

Скопируй `.env.example` в `.env`:

- `VITE_API_URL` — базовый URL API. Пусто = тот же хост (эндпоинты начинаются с `/api/v1`).
  В dev удобно оставить пустым и пользоваться прокси Vite на `http://localhost:8000`
  (см. `vite.config.ts` → `server.proxy`).
- `VITE_DEV_INIT_DATA` — dev-мок `initData` для заголовка `X-TG-Init-Data`, когда приложение
  открыто в обычном браузере (вне Telegram). Возьми валидную подписанную строку `initData`
  из реального запуска бота, иначе бэкенд отклонит подпись (401).

## Dev-мок Telegram

- В `index.html` подключён официальный `telegram-web-app.js`.
- Если `window.Telegram.WebApp.initData` пустой (обычный браузер), приложение:
  - подставляет заголовок `X-TG-Init-Data` из `VITE_DEV_INIT_DATA`;
  - применяет дефолтные CSS-переменные темы (light/dark по `prefers-color-scheme`),
    чтобы UI был виден без Telegram;
  - haptic-вызовы становятся no-op.
- Внутри Telegram: `tg.ready()` + `tg.expand()`, тема из `themeParams`, реакция на `themeChanged`,
  haptic на успешных свайп-переходах.

## Архитектура

```
src/
  app/navigation.ts        # reactive таб-навигация + drill-down канал (без vue-router)
  shared/
    api/http.ts            # fetch-клиент, X-TG-Init-Data на всех запросах, ApiError
    api/endpoints.ts       # типизированные вызовы (пути строго по контракту)
    api/types.ts           # типы контракта
    telegram/webapp.ts     # обёртка WebApp: init, initData, тема, haptic
    ui/                    # AuthImage, StatusBadge, Money, BottomSheet, ToastHost
    utils/                 # format (деньги/%/дни), status (FSM/цвета), uuid
  stores/                  # Pinia: session, items (оптимистика/откат), scout, analytics, toast
  components/              # BottomNav, ItemCard (свайпы), FilterSheet, SellPriceSheet, Autocomplete
  screens/                 # Inventory, Create, Scout, Bi, Settings
  App.vue, main.ts
```

## Экраны

1. **Склад** — виртуализированный список (`@vueuse/core` `useVirtualList`).
   Свайп вправо = следующий статус (Optimistic Update + `Idempotency-Key` + `version`, откат при 409).
   Свайп влево = архивация с Toast «Отменить» (undo ~5 c, реальный `DELETE` только если не отменили).
   Поиск (debounce 300 мс), фильтры (Bottom Sheet), FAB «+».
   Перед переходом в `SOLD` спрашивается цена продажи.
2. **Создание** — один длинный скролл, автодополнение бренда/категории, sticky-кнопка «Сохранить»
   с защитой от двойного сабмита. Блок финансов скрыт для роли `EMPLOYEE`.
3. **Scout** — тёмный экран, круглая кнопка-микрофон (Web Speech API, `ru-RU`), fallback — текстовый ввод.
   Карточка-вердикт BUY/AVOID/UNKNOWN + история последних 3 запросов.
4. **BI** — только `OWNER`/`ANALYST`: KPI, «зависшие товары» (drill-down на Склад по `ids`),
   окупаемость по точкам (таблица, tabular-nums), оборачиваемость (SVG-бары).
5. **Настройки** — переключатель активного склада, команда (список/приглашение/отзыв — только OWNER),
   текущая роль, «Выгрузить в CSV» (заглушка — реального эндпоинта пока нет).

## Роли и финансы

- BI-вкладка скрыта для `EMPLOYEE`.
- Финансовые поля в `ItemOut` приходят `null` для `EMPLOYEE` — финансовые блоки не показываются.
- Видимость финансов/BI определяется по роли текущего склада (`OWNER`/`ANALYST`).

## Замечания по контракту

- `X-TG-Init-Data` — на всех запросах; `Idempotency-Key` (UUID) — дополнительно на `PATCH .../status`.
- Медиа грузится через `fetch` (с заголовком) → `blob` → `objectURL` (компонент `AuthImage`),
  т.к. тег `<img>` не передаёт заголовок `initData`.
- Список инвайтов бэкенд не отдаёт (нет GET), поэтому pending-приглашения для отзыва
  отслеживаются локально в рамках сессии.
