<script setup lang="ts">
/** Плоский bottom sheet с overlay, safe-area и закрытием по свайпу вниз/тапу по фону. */
import { ref, watch } from 'vue'

const props = withDefaults(
  defineProps<{ modelValue: boolean; title?: string }>(),
  { title: '' },
)
const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

const dragY = ref(0)
let startY = 0
let dragging = false

function close(): void {
  emit('update:modelValue', false)
}

watch(
  () => props.modelValue,
  (open) => {
    dragY.value = 0
    document.body.style.overflow = open ? 'hidden' : ''
  },
)

function onDown(e: PointerEvent): void {
  dragging = true
  startY = e.clientY
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
}
function onMove(e: PointerEvent): void {
  if (!dragging) return
  const dy = e.clientY - startY
  dragY.value = Math.max(0, dy)
}
function onUp(): void {
  if (!dragging) return
  dragging = false
  if (dragY.value > 90) close()
  else dragY.value = 0
}
</script>

<template>
  <Teleport to="body">
    <Transition name="sheet">
      <div v-if="modelValue" class="overlay" @click.self="close">
        <div class="sheet" :style="{ transform: `translateY(${dragY}px)` }">
          <div
            class="grab-zone"
            @pointerdown="onDown"
            @pointermove="onMove"
            @pointerup="onUp"
            @pointercancel="onUp"
          >
            <div class="grabber" />
          </div>
          <div v-if="title" class="sheet-title">{{ title }}</div>
          <div class="sheet-body no-scrollbar">
            <slot />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  /*
   * Выше полноэкранных оверлеев (карточка вещи, шаблоны, каналы,
   * контент-план — у всех 120), иначе шторка открывается ЗА ними и
   * выглядит так, будто нажатие ничего не сделало. Ниже тостов (200),
   * чтобы сообщения оставались поверх.
   */
  z-index: 150;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: flex-end;
}
.sheet {
  width: 100%;
  max-height: 85vh;
  background: var(--tg-theme-bg-color);
  border-top-left-radius: var(--radius);
  border-top-right-radius: var(--radius);
  display: flex;
  flex-direction: column;
  padding-bottom: calc(var(--safe-bottom) + 12px);
  will-change: transform;
}
.grab-zone {
  padding: 8.5px 0 3.5px;
  display: flex;
  justify-content: center;
  touch-action: none;
  cursor: grab;
}
.grabber {
  width: 31.5px;
  height: 3.5px;
  border-radius: 2px;
  background: var(--tg-theme-hint-color);
  opacity: 0.4;
}
.sheet-title {
  font-size: 15px;
  font-weight: 700;
  padding: 3.5px 14px 7px;
}
.sheet-body {
  padding: 3.5px 14px 7px;
  overflow-y: auto;
}

.sheet-enter-active,
.sheet-leave-active {
  transition: opacity 0.2s ease;
}
.sheet-enter-active .sheet,
.sheet-leave-active .sheet {
  transition: transform 0.25s cubic-bezier(0.22, 1, 0.36, 1);
}
.sheet-enter-from,
.sheet-leave-to {
  opacity: 0;
}
.sheet-enter-from .sheet,
.sheet-leave-to .sheet {
  transform: translateY(100%);
}
</style>
