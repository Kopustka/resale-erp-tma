<script setup lang="ts">
import { defineAsyncComponent, onMounted, ref, watch } from 'vue'
import { nav } from '@/app/navigation'
import type { Tab } from '@/app/navigation'
import { useSessionStore } from '@/stores/session'
import BottomNav from '@/components/BottomNav.vue'
import ToastHost from '@/shared/ui/ToastHost.vue'
import InventoryScreen from '@/screens/InventoryScreen.vue'
import BiScreen from '@/screens/BiScreen.vue'
import SettingsScreen from '@/screens/SettingsScreen.vue'

/*
 * Оверлеи грузим по требованию. Раньше вся пятёрка попадала в стартовый
 * бандл, хотя открывается по одному и не сразу: карточка, создание,
 * шаблоны, каналы, контент-план. Их код тянулся при каждом запуске
 * мини-аппа — а это первое, что видит клиент.
 */
const CreateScreen = defineAsyncComponent(() => import('@/screens/CreateScreen.vue'))
const ItemDetail = defineAsyncComponent(() => import('@/screens/ItemDetail.vue'))
const TemplatesScreen = defineAsyncComponent(() => import('@/screens/TemplatesScreen.vue'))
const ChannelsScreen = defineAsyncComponent(() => import('@/screens/ChannelsScreen.vue'))
const CalendarScreen = defineAsyncComponent(() => import('@/screens/CalendarScreen.vue'))
const AdminScreen = defineAsyncComponent(() => import('@/screens/AdminScreen.vue'))


const session = useSessionStore()

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
    void import('@/screens/TemplatesScreen.vue')
    void import('@/screens/ChannelsScreen.vue')
    void import('@/screens/CalendarScreen.vue')
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
    <!-- Начальная загрузка сессии -->
    <div v-if="!session.ready && session.loading" class="boot hint">Загрузка…</div>

    <div v-else-if="session.error" class="boot">
      <p class="negative">{{ session.error }}</p>
      <button class="boot-retry" @click="session.init()">Повторить</button>
    </div>

    <div v-else-if="session.ready && session.stores.length === 0" class="boot">
      <p>Нет доступных складов.</p>
      <p class="hint">Попросите владельца добавить вас в команду через бота.</p>
    </div>

    <template v-else-if="session.ready">
      <main class="viewport">
        <InventoryScreen v-show="nav.activeTab === 'inventory'" />
        <!--
          Вкладки монтируем при первом заходе, дальше держим через v-show.
          Раньше все три монтировались сразу, и настройки с аналитикой на
          старте тянули шесть лишних запросов до того, как показался склад.
        -->
        <BiScreen
          v-if="seen.has('bi') && session.canSeeFinance"
          v-show="nav.activeTab === 'bi'"
        />
        <SettingsScreen v-if="seen.has('settings')" v-show="nav.activeTab === 'settings'" />
      </main>

      <BottomNav />
    </template>

    <!-- Оверлей создания товара -->
    <CreateScreen v-if="nav.overlay === 'create'" />
    <!-- Оверлей детали/редактирования -->
    <ItemDetail v-if="nav.overlay === 'detail'" />
    <!-- Оверлей шаблонов постов (только OWNER) -->
    <TemplatesScreen v-if="nav.overlay === 'templates'" />
    <!-- Оверлей каналов автопостинга (только OWNER) -->
    <ChannelsScreen v-if="nav.overlay === 'channels'" />
    <!-- Оверлей контент-плана (только OWNER) -->
    <CalendarScreen v-if="nav.overlay === 'calendar'" />
    <!-- Оверлей админ-панели (только OWNER) -->
    <AdminScreen v-if="nav.overlay === 'admin'" />

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
.boot {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 40px 24px;
  text-align: center;
}
.boot-retry {
  color: var(--tg-theme-link-color);
  font-weight: 700;
  padding: 10px;
}
</style>
