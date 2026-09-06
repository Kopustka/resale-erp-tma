<script setup lang="ts">
import { computed } from 'vue'
import { nav, setTab, type Tab } from '@/app/navigation'
import { useSessionStore } from '@/stores/session'
import { hapticSelection } from '@/shared/telegram/webapp'

const session = useSessionStore()

interface TabDef {
  key: Tab
  label: string
  icon: string
}

const ALL_TABS: TabDef[] = [
  { key: 'inventory', label: 'Склад', icon: 'inventory' },
  { key: 'bi', label: 'BI', icon: 'bi' },
  { key: 'settings', label: 'Настройки', icon: 'settings' },
]

// BI скрыт для EMPLOYEE (видна только OWNER/ANALYST).
const tabs = computed(() =>
  ALL_TABS.filter((t) => (t.key === 'bi' ? session.canSeeFinance : true)),
)

function onTab(tab: Tab): void {
  if (nav.activeTab !== tab) hapticSelection()
  setTab(tab)
}
</script>

<template>
  <nav class="bottom-nav">
    <button
      v-for="t in tabs"
      :key="t.key"
      class="nav-btn tap"
      :class="{ active: nav.activeTab === t.key }"
      :aria-label="t.label"
      :aria-current="nav.activeTab === t.key ? 'page' : undefined"
      @click="onTab(t.key)"
    >
      <span class="nav-icon">
        <svg
          viewBox="0 0 24 24"
          width="23"
          height="23"
          fill="none"
          stroke="currentColor"
          stroke-width="1.9"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          <template v-if="t.icon === 'inventory'">
            <path d="M4 8.5 12 4l8 4.5v7L12 20l-8-4.5v-7Z" />
            <path d="m4 8.5 8 4.5 8-4.5M12 13v7" />
          </template>
          <template v-else-if="t.icon === 'bi'">
            <path d="M5 19V11M12 19V5M19 19v-6" />
          </template>
          <template v-else>
            <circle cx="12" cy="12" r="3.2" />
            <path d="M12 3v2.2M12 18.8V21M21 12h-2.2M5.2 12H3M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6M18.4 18.4l-1.6-1.6M7.2 7.2 5.6 5.6" />
          </template>
        </svg>
      </span>
      <span class="nav-label">{{ t.label }}</span>
    </button>
  </nav>
</template>

<style scoped>
/* Разделительной линии нет: список уходит под панель, и вместо шва — мягкое
   растворение фона. Так нижний край не режет карточки пополам. */
.bottom-nav {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 50;
  display: flex;
  height: calc(var(--nav-height) + var(--safe-bottom));
  padding: 8px 12px var(--safe-bottom);
  background: linear-gradient(180deg, transparent, var(--ink-0) 34%);
}
.nav-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3.5px;
  color: var(--fg-2);
  transition: color 0.15s ease;
}
.nav-btn.active {
  color: var(--brand);
}
.nav-icon {
  display: flex;
}
.nav-label {
  font-size: 9.5px;
  font-weight: 650;
  letter-spacing: -0.005em;
}
</style>
