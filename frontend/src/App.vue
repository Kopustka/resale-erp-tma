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
      <main class="viewport" :class="{ pushed: overlay?.motion === 'push' }">
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
   Анимируем только transform и opacity: их считает композитор, и на слабом
   телефоне не появляется рывков. Раскладку не трогаем вовсе.

   Кривая — «выброс и торможение»: движение начинается резко и мягко
   гаснет. Линейная или ease-in-out на таком расстоянии читается как
   вязкая, будто экран едет по маслу. */
.ov-lift-enter-active,
.ov-push-enter-active {
  transition: transform 0.34s cubic-bezier(0.16, 0.84, 0.24, 1);
  will-change: transform;
}
.ov-lift-leave-active,
.ov-push-leave-active {
  transition: transform 0.26s cubic-bezier(0.4, 0, 0.6, 1);
  will-change: transform;
}

/* Форма приходит снизу и слегка проявляется: она ложится поверх, и полупро-
   зрачность в начале подсказывает, что нижний экран никуда не делся. */
.ov-lift-enter-active,
.ov-lift-leave-active {
  transition-property: transform, opacity;
}
.ov-lift-enter-from,
.ov-lift-leave-to {
  transform: translate3d(0, 100%, 0);
  opacity: 0.7;
}

/* Переход вглубь приходит от самого края и БЕЗ прозрачности: настоящий
   экран не просвечивает. Прежняя версия выезжала с 14% и одновременно
   проявлялась — получалось короткое мутное пятно вместо движения. */
.ov-push-enter-from,
.ov-push-leave-to {
  transform: translate3d(100%, 0, 0);
}
/* Тень по левой кромке отделяет въезжающий экран от нижнего. Рисуется один
   раз и едет вместе со слоем, поэтому ничего не пересчитывается. */
.ov-push-enter-active,
.ov-push-leave-active,
.ov-push-enter-to {
  box-shadow: -14px 0 28px rgba(0, 0, 0, 0.35);
}

/* Нижний слой подаётся назад — от этого движение читается как глубина, а
   не как две несвязанные картинки. Сдвиг небольшой: он лишь намекает. */
.viewport {
  transition:
    transform 0.34s cubic-bezier(0.16, 0.84, 0.24, 1),
    filter 0.34s ease-out;
}
.viewport.pushed {
  transform: translate3d(-18%, 0, 0);
  filter: brightness(0.72);
}

@media (prefers-reduced-motion: reduce) {
  .viewport > .shown {
    animation: none;
  }
  .ov-lift-enter-active,
  .ov-push-enter-active,
  .ov-lift-leave-active,
  .ov-push-leave-active,
  .viewport {
    transition-duration: 0.01ms;
  }
  .viewport.pushed {
    transform: none;
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
