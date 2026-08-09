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
        <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
          <template v-if="t.icon === 'inventory'">
            <path
              fill="currentColor"
              d="M3 4h18v4H3V4zm1 6h16v10H4V10zm4 3v2h8v-2H8z"
            />
          </template>
          <template v-else-if="t.icon === 'bi'">
            <path fill="currentColor" d="M4 13h4v7H4v-7zm6-8h4v15h-4V5zm6 4h4v11h-4V9z" />
          </template>
          <template v-else>
            <path
              fill="currentColor"
              d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm9 4a7.9 7.9 0 0 0-.15-1.5l2-1.55-2-3.46-2.35.94a8 8 0 0 0-2.6-1.5L15.5 2h-7l-.4 2.43a8 8 0 0 0-2.6 1.5L3.15 5 1.15 8.46l2 1.55A8 8 0 0 0 3 12a8 8 0 0 0 .15 1.5l-2 1.55 2 3.46 2.35-.94a8 8 0 0 0 2.6 1.5L8.5 22h7l.4-2.43a8 8 0 0 0 2.6-1.5l2.35.94 2-3.46-2-1.55c.1-.49.15-1 .15-1.5z"
            />
          </template>
        </svg>
      </span>
      <span class="nav-label">{{ t.label }}</span>
    </button>
  </nav>
</template>

<style scoped>
.bottom-nav {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 50;
  display: flex;
  height: calc(var(--nav-height) + var(--safe-bottom));
  padding-bottom: var(--safe-bottom);
  background: var(--tg-theme-bg-color);
  border-top: 1px solid var(--tg-theme-secondary-bg-color);
}
.nav-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  color: var(--tg-theme-hint-color);
  transition: color 0.15s ease;
}
.nav-btn.active {
  color: var(--tg-theme-link-color);
}
.nav-icon {
  display: flex;
}
.nav-label {
  font-size: 11px;
  font-weight: 600;
}
</style>
