<script setup lang="ts">
import { computed, onMounted, ref, shallowRef, watch } from 'vue'
import type { Component } from 'vue'
import { nav } from '@/app/navigation'
import type { Tab } from '@/app/navigation'
import { useSessionStore } from '@/stores/session'
import { isInTelegram } from '@/shared/telegram/webapp'
import BottomNav from '@/components/BottomNav.vue'
import ToastHost from '@/shared/ui/ToastHost.vue'
import InventoryScreen from '@/screens/InventoryScreen.vue'
import BiScreen from '@/screens/BiScreen.vue'
import SettingsScreen from '@/screens/SettingsScreen.vue'

/*
 * Оверлеи грузим по требованию: их код не нужен на старте, а старт —
 * первое, что видит клиент.
 */
/**
 * Загрузчики оверлеев. Держим сам модуль, а не defineAsyncComponent.
 *
 * Разница существенная. Отложенный компонент монтируется через промис, то
 * есть на такт позже переключения состояния — и <Transition> не успевает
 * поставить начальный кадр: экран возникал сразу на месте, без движения, а
 * подложка при этом уже ехала. Получались два несинхронных слоя.
 *
 * Готовый компонент монтируется в том же такте, и анимация входа
 * проигрывается целиком. Куски кода при этом остаются отдельными: грузим их
 * в простое, до первого открытия.
 */
const LOADERS = {
  create: () => import('@/screens/CreateScreen.vue'),
  detail: () => import('@/screens/ItemDetail.vue'),
  admin: () => import('@/screens/AdminScreen.vue'),
  fields: () => import('@/screens/FieldsScreen.vue'),
} as const

type OverlayKey = keyof typeof LOADERS

const MOTION: Record<OverlayKey, 'lift' | 'push'> = {
  create: 'lift',
  detail: 'push',
  admin: 'push',
  fields: 'push',
}

const loaded = shallowRef<Partial<Record<OverlayKey, Component>>>({})

async function warm(key: OverlayKey): Promise<void> {
  if (loaded.value[key]) return
  const mod = await LOADERS[key]()
  loaded.value = { ...loaded.value, [key]: mod.default }
}


const session = useSessionStore()

/**
 * Мини-апп открыли вне Telegram — по ссылке из браузера. Без подписанного
 * initData бэкенд отвечает отказом, и раньше человек видел голое «No hash in
 * initData» посреди пустого экрана и решал, что сервис сломан. Проверяем
 * заранее и объясняем словами, куда идти.
 */
const outsideTelegram = !isInTelegram()
const botUrl = import.meta.env.VITE_BOT_URL || ''

/**
 * Оверлеи различаются по смыслу, поэтому и движутся по-разному.
 *
 * Создание вещи — форма поверх текущего экрана: приходит снизу и уходит
 * вниз, как лист бумаги, который положили сверху и убрали.
 *
 * Карточка вещи, админка и поля — переход вглубь: приходят справа и уходят
 * вправо. Так видно, что это «дальше», а не «поверх», и возврат ощущается
 * возвратом, а не закрытием.
 */
const overlay = computed(() => {
  const key = nav.overlay as OverlayKey | null
  if (!key || !(key in LOADERS)) return null
  const comp = loaded.value[key]
  return comp ? { comp, motion: MOTION[key] } : null
})

// Открыли раньше, чем кусок кода догрузился в простое — грузим по месту.
watch(
  () => nav.overlay,
  (key) => {
    if (key && key in LOADERS) void warm(key as OverlayKey)
  },
)

/**
 * Вкладки, которые пользователь уже открывал. Пока вкладку не трогали,
 * её компонент не смонтирован и данные не запрашиваются.
 */
const seen = ref(new Set<Tab>(['inventory']))
watch(
  () => nav.activeTab,
  (tab) => {
    if (!seen.value.has(tab)) seen.value = new Set(seen.value).add(tab)
  },
  { immediate: true },
)

/**
 * Подтягиваем чанки оверлеев в простое, после первой отрисовки. Так старт
 * остаётся лёгким, но к моменту, когда пользователь откроет карточку, код
 * уже в кэше — открытие мгновенное, без подгрузки по тапу.
 */
function prefetchOverlays(): void {
  const load = () => {
    // Все четыре, а не два. Незагруженный экран открывается без анимации:
    // он появляется уже на месте, потому что монтируется позже такта, в
    // котором переход должен был начаться.
    for (const key of Object.keys(LOADERS) as OverlayKey[]) void warm(key)
  }
  const idle = (window as unknown as { requestIdleCallback?: (cb: () => void) => void })
    .requestIdleCallback
  if (idle) idle(load)
  else window.setTimeout(load, 1500)
}

onMounted(() => {
  void session.init()
  prefetchOverlays()
})
</script>

<template>
  <div class="app-shell">
    <!-- Открыто вне Telegram: подпись проверить нечем, объясняем куда идти -->
    <div v-if="outsideTelegram" class="boot">
      <div class="boot-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor"
             stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 8.5 12 4l8 4.5v7L12 20l-8-4.5v-7Z" /><path d="m4 8.5 8 4.5 8-4.5M12 13v7" />
        </svg>
      </div>
      <h1 class="boot-title">Склад открывается в Telegram</h1>
      <p class="boot-text">
        Это мини-приложение работает внутри бота: он подтверждает, что склад ваш.
        Откройте его оттуда.
      </p>
      <a v-if="botUrl" class="boot-cta" :href="botUrl">Открыть бота</a>
    </div>

    <!-- Начальная загрузка сессии -->
    <div v-else-if="!session.ready && session.loading" class="boot">
      <div class="boot-spin" aria-hidden="true" />
    </div>

    <div v-else-if="session.error" class="boot">
      <h1 class="boot-title">Не удалось открыть склад</h1>
      <p class="boot-text">{{ session.error }}</p>
      <button class="boot-cta" @click="session.init()">Повторить</button>
    </div>

    <div v-else-if="session.ready && session.stores.length === 0" class="boot">
      <h1 class="boot-title">Складов пока нет</h1>
      <p class="boot-text">
        Попросите владельца добавить вас в команду — доступ появится сразу после этого.
      </p>
    </div>

    <template v-else-if="session.ready">
      <main class="viewport">
        <!--
          Вкладки лежат друг на друге и перекрещиваются прозрачностью, а не
          подменяются. Раньше здесь был v-show, то есть display: none, — по
          нему переход не проигрывается вообще, и вкладка возникала резко.
          Первую вкладку монтируем сразу, остальные — при первом заходе:
          иначе настройки и аналитика на старте тянули бы лишние запросы до
          того, как покажется склад.
        -->
        <InventoryScreen :class="{ shown: nav.activeTab === 'inventory' }" />
        <BiScreen
          v-if="seen.has('bi') && session.canSeeFinance"
          :class="{ shown: nav.activeTab === 'bi' }"
        />
        <SettingsScreen
          v-if="seen.has('settings')"
          :class="{ shown: nav.activeTab === 'settings' }"
        />
      </main>

      <BottomNav />
    </template>

    <Transition name="ov-fade">
      <component :is="overlay.comp" v-if="overlay" />
    </Transition>

    <ToastHost />
  </div>
</template>

<style scoped>
.app-shell {
  height: 100vh;
  height: 100dvh;
  overflow: hidden;
  background: var(--tg-theme-bg-color);
}
.viewport {
  position: relative;
  height: 100%;
}
/*
 * Вкладки лежат стопкой и перекрещиваются прозрачностью: уходящая гаснет,
 * приходящая проявляется, обе занимают одно место. Скрытая не перехватывает
 * нажатия — иначе кнопки невидимого экрана ловили бы касания поверх нужного.
 */
.viewport > * {
  position: absolute;
  inset: 0;
  padding-bottom: calc(var(--nav-height) + var(--safe-bottom));
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.22s ease;
}
.viewport > .shown {
  opacity: 1;
  pointer-events: auto;
  /* Переход работает, когда меняется значение, а при первом заходе вкладка
     рождается уже видимой — менять нечего, и она возникала рывком. Анимация
     стартует и от появления элемента, поэтому первый показ тоже плавный. */
  animation: tab-fade 0.22s ease;
}
@keyframes tab-fade {
  from {
    opacity: 0;
  }
}

/* --- Экраны поверх -------------------------------------------------------
   Затухание, а не выезд. Анимируем только opacity: её считает композитор,
   раскладку не трогаем вовсе, поэтому на слабом телефоне нет рывков.
   Появление чуть медленнее исчезновения: приходящий экран должен успеть
   прочитаться, а уходящий не задерживать. */
.ov-fade-enter-active {
  transition: opacity 0.24s ease-out;
}
.ov-fade-leave-active {
  transition: opacity 0.18s ease-in;
}
.ov-fade-enter-from,
.ov-fade-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .viewport > *,
  .ov-fade-enter-active,
  .ov-fade-leave-active {
    transition-duration: 0.01ms;
  }
  .viewport > .shown {
    animation: none;
  }
}
.boot {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 35px 24.5px;
  text-align: center;
}
.boot-mark {
  width: 54px;
  height: 54px;
  border-radius: 17.5px;
  display: grid;
  place-items: center;
  background: var(--ink-1);
  color: var(--brand);
  margin-bottom: 2px;
}
.boot-title {
  margin: 0;
  font-size: 19px;
  line-height: 23.5px;
  font-weight: 700;
  letter-spacing: -0.02em;
}
.boot-text {
  margin: 0;
  max-width: 30ch;
  font-size: 12.5px;
  line-height: 17.5px;
  color: var(--fg-1);
}
.boot-cta {
  margin-top: 7px;
  min-width: 156.5px;
  height: 43.5px;
  padding: 0 19px;
  border-radius: var(--r-field);
  background: var(--brand);
  color: var(--brand-ink);
  font-size: 13px;
  font-weight: 650;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  text-decoration: none;
}
.boot-spin {
  width: 22.5px;
  height: 22.5px;
  border-radius: 50%;
  border: 2.5px solid var(--ink-3);
  border-top-color: var(--brand);
  animation: boot-turn 0.7s linear infinite;
}
@keyframes boot-turn {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .boot-spin {
    animation-duration: 2s;
  }
}
</style>
