<script setup lang="ts">
/** Хост тостов. Авто-дисмисс по duration, поддержка action (Undo). */
import { onBeforeUnmount, watch } from 'vue'
import { useToastStore } from '@/stores/toast'

const store = useToastStore()
const timers = new Map<number, number>()

watch(
  () => store.toasts.map((t) => t.id),
  (ids) => {
    // Ставим таймеры новым тостам.
    for (const t of store.toasts) {
      if (!timers.has(t.id)) {
        const handle = window.setTimeout(() => {
          timers.delete(t.id)
          store.dismiss(t.id)
        }, t.duration)
        timers.set(t.id, handle)
      }
    }
    // Чистим таймеры исчезнувших.
    for (const [id, handle] of timers) {
      if (!ids.includes(id)) {
        clearTimeout(handle)
        timers.delete(id)
      }
    }
  },
  { deep: true },
)

function onAction(id: number): void {
  const handle = timers.get(id)
  if (handle) {
    clearTimeout(handle)
    timers.delete(id)
  }
  store.trigger(id)
}

onBeforeUnmount(() => {
  for (const handle of timers.values()) clearTimeout(handle)
  timers.clear()
})
</script>

<template>
  <div class="toast-host">
    <TransitionGroup name="toast">
      <div v-for="t in store.toasts" :key="t.id" class="toast" :class="`toast-${t.kind}`">
        <span class="toast-msg">{{ t.message }}</span>
        <button v-if="t.actionLabel" class="toast-action" @click="onAction(t.id)">
          {{ t.actionLabel }}
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  left: 0;
  right: 0;
  bottom: calc(var(--nav-height) + var(--safe-bottom) + 12px);
  z-index: 200;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  max-width: 480px;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
  border-left: 3px solid var(--tg-theme-hint-color);
  font-size: 14px;
}
.toast-success {
  border-left-color: var(--accent-positive);
}
.toast-error {
  border-left-color: var(--tg-theme-destructive-text-color);
}
.toast-msg {
  flex: 1;
  min-width: 0;
}
.toast-action {
  flex: none;
  font-weight: 700;
  color: var(--tg-theme-link-color);
  padding: 6px 8px;
  min-height: 32px;
}

.toast-enter-active,
.toast-leave-active {
  transition: all 0.2s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
