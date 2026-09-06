<script setup lang="ts">
import { computed, defineAsyncComponent, onMounted, ref, watch } from 'vue'
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
const CreateScreen = defineAsyncComponent(() => import('@/screens/CreateScreen.vue'))
const ItemDetail = defineAsyncComponent(() => import('@/screens/ItemDetail.vue'))
const AdminScreen = defineAsyncComponent(() => import('@/screens/AdminScreen.vue'))


const session = useSessionStore()

/**
 * Мини-апп открыли вне Telegram — по ссылке из браузера. Без подписанного
 * initData бэкенд отвечает отказом, и раньше человек видел голое «No hash in
 * initData» посреди пустого экрана и решал, что сервис сломан. Проверяем
 * заранее и объясняем словами, куда идти.
 */
const outsideTelegram = !isInTelegram()

/**
 * Оверлеи различаются по смыслу, поэтому и движутся по-разному.
 *
 * Создание вещи — форма поверх текущего экрана: приходит снизу и уходит
 * вниз, как лист бумаги, который положили сверху и убрали.
 *
 * Карточка вещи и админка — переход вглубь: приходят справа и уходят
 * вправо. Так видно, что это не «поверх», а «дальше», и возврат ощущается
 * возвратом, а не закрытием.
 */
const OVERLAYS = {
  create: { comp: CreateScreen, motion: 'lift' },
  detail: { comp: ItemDetail, motion: 'push' },
  admin: { comp: AdminScreen, motion: 'push' },
} as const

const overlay = computed(() =>
  nav.overlay ? (OVERLAYS[nav.overlay as keyof typeof OVERLAYS] ?? null) : null,
)
const botUrl = import.meta.env.VITE_BOT_URL || ''

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
    void import('@/screens/ItemDetail.vue')
    void import('@/screens/CreateScreen.vue')
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
        <InventoryScreen
          v-show="nav.activeTab === 'inventory'"
          :class="{ shown: nav.activeTab === 'inventory' }"
        />
        <!--
          Вкладки монтируем при первом заходе, дальше держим через v-show.
          Раньше все три монтировались сразу, и настройки с аналитикой на
          старте тянули шесть лишних запросов до того, как показался склад.
        -->
        <BiScreen
          v-if="seen.has('bi') && session.canSeeFinance"
          v-show="nav.activeTab === 'bi'"
          :class="{ shown: nav.activeTab === 'bi' }"
        />
        <SettingsScreen
          v-if="seen.has('settings')"
          v-show="nav.activeTab === 'settings'"
          :class="{ shown: nav.activeTab === 'settings' }"
        />
      </main>

      <BottomNav />
    </template>

    <Transition :name="overlay ? `ov-${overlay.motion}` : 'ov-lift'">
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
  height: 100%;
  padding-bottom: calc(var(--nav-height) + var(--safe-bottom));
}
.viewport > * {
  height: 100%;
}
/*
 * Вкладки переключаются через v-show, то есть display: none. Переход по
 * этому свойству не проигрывается, поэтому берём анимацию: класс снимается
 * при уходе с вкладки и ставится обратно при возврате, а вместе с ним
 * заново запускается и анимация.
 *
 * Движение короткое и почти незаметное: вкладка должна появляться сразу,
 * а не выезжать — иначе интерфейс начинает казаться медленным.
 */
.viewport > .shown {
  animation: tab-in 170ms cubic-bezier(0.22, 1, 0.36, 1);
}
@keyframes tab-in {
  from {
    opacity: 0;
    transform: translate3d(0, 6px, 0);
  }
}

/* --- Оверлеи ------------------------------------------------------------
   Анимируем только transform и opacity: их считает композитор, и на
   слабом телефоне не появляется рывков. Плоскости внизу не двигаем —
   их всё равно перекрывает оверлей во весь экран. */
.ov-lift-enter-active,
.ov-push-enter-active {
  transition:
    transform 0.26s cubic-bezier(0.32, 0.72, 0, 1),
    opacity 0.18s ease-out;
  will-change: transform;
}
.ov-lift-leave-active,
.ov-push-leave-active {
  transition:
    transform 0.22s cubic-bezier(0.32, 0.72, 0, 1),
    opacity 0.16s ease-in;
  will-change: transform;
}
.ov-lift-enter-from,
.ov-lift-leave-to {
  transform: translate3d(0, 100%, 0);
  opacity: 0.6;
}
.ov-push-enter-from,
.ov-push-leave-to {
  transform: translate3d(14%, 0, 0);
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .viewport > .shown {
    animation: none;
  }
  .ov-lift-enter-active,
  .ov-push-enter-active,
  .ov-lift-leave-active,
  .ov-push-leave-active {
    transition-duration: 0.01ms;
  }
}
.boot {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 40px 28px;
  text-align: center;
}
.boot-mark {
  width: 62px;
  height: 62px;
  border-radius: 20px;
  display: grid;
  place-items: center;
  background: var(--ink-1);
  color: var(--brand);
  margin-bottom: 2px;
}
.boot-title {
  margin: 0;
  font-size: 22px;
  line-height: 27px;
  font-weight: 700;
  letter-spacing: -0.02em;
}
.boot-text {
  margin: 0;
  max-width: 30ch;
  font-size: 14.5px;
  line-height: 20px;
  color: var(--fg-1);
}
.boot-cta {
  margin-top: 8px;
  min-width: 180px;
  height: 50px;
  padding: 0 22px;
  border-radius: var(--r-field);
  background: var(--brand);
  color: var(--brand-ink);
  font-size: 15px;
  font-weight: 650;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  text-decoration: none;
}
.boot-spin {
  width: 26px;
  height: 26px;
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
