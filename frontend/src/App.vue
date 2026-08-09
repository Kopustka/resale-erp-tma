<script setup lang="ts">
import { onMounted } from 'vue'
import { nav } from '@/app/navigation'
import { useSessionStore } from '@/stores/session'
import BottomNav from '@/components/BottomNav.vue'
import ToastHost from '@/shared/ui/ToastHost.vue'
import InventoryScreen from '@/screens/InventoryScreen.vue'
import BiScreen from '@/screens/BiScreen.vue'
import SettingsScreen from '@/screens/SettingsScreen.vue'
import CreateScreen from '@/screens/CreateScreen.vue'
import ItemDetail from '@/screens/ItemDetail.vue'

const session = useSessionStore()

onMounted(() => {
  void session.init()
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
        <BiScreen v-show="nav.activeTab === 'bi' && session.canSeeFinance" />
        <SettingsScreen v-show="nav.activeTab === 'settings'" />
      </main>

      <BottomNav />
    </template>

    <!-- Оверлей создания товара -->
    <CreateScreen v-if="nav.overlay === 'create'" />
    <!-- Оверлей детали/редактирования -->
    <ItemDetail v-if="nav.overlay === 'detail'" />

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
