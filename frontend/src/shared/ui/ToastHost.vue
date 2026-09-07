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
        <span v-if="t.count > 1" class="toast-count">{{ t.count }}</span>
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
  gap: 7px;
  padding: 0 10.5px;
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  max-width: 417.5px;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10.5px;
  padding: 10.5px 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
  border-left: 3px solid var(--tg-theme-hint-color);
  font-size: 12px;
}
.toast-success {
  border-left-color: var(--accent-positive);
}
.toast-error {
  border-left-color: var(--tg-theme-destructive-text-color);
}
.toast-count {
  flex: none;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.16);
  font-size: 11px;
  font-weight: 700;
  line-height: 18px;
  text-align: center;
}
.toast-msg {
  flex: 1;
  min-width: 0;
}
.toast-action {
  flex: none;
  font-weight: 700;
  color: var(--tg-theme-link-color);
  padding: 5px 7px;
  min-height: 28px;
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
