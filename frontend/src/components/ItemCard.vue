<script setup lang="ts">
/**
 * Карточка товара со свайпами:
 *  - вправо  -> следующий статус (emit 'next')
 *  - влево   -> архивация (emit 'archive')
 *  - тап     -> деталь (emit 'open')
 *
 * Механика жеста:
 *  - ось определяется с угловым перевесом (горизонталь должна явно доминировать);
 *  - как только ось = X, вертикальный скролл страницы БЛОКИРУЕТСЯ
 *    (touchmove preventDefault, passive: false) до конца жеста;
 *  - за порогом срабатывания движение «тяжелеет» (резина), чтобы карточка
 *    не улетала за палец;
 *  - пересечение порога подсвечивает фон и даёт лёгкий haptic-тик;
 *  - transition выключен только пока палец на экране — завершение всегда плавное.
 */
import { computed, onBeforeUnmount, ref } from 'vue'
import type { ItemOut } from '@/shared/api/types'
import AuthImage from '@/shared/ui/AuthImage.vue'
import StatusBadge from '@/shared/ui/StatusBadge.vue'
import Money from '@/shared/ui/Money.vue'
import { isSoldLike, nextStatus, STATUS_LABELS } from '@/shared/utils/status'
import { hapticSelection } from '@/shared/telegram/webapp'

const props = withDefaults(
  defineProps<{
    item: ItemOut
    showFinance: boolean
    swipeable?: boolean
    /** Идёт фоновая AI-генерация названия/описания. */
    generating?: boolean
  }>(),
  { swipeable: true, generating: false },
)
const emit = defineEmits<{ next: []; archive: []; open: [] }>()

const THRESHOLD = 84
const MAX_LEFT = 150 // дальше влево карточку не тянем (до подтверждения)
const OVERDRAG = 0.35 // «вязкость» после порога

const dx = ref(0)
const dragging = ref(false)
const removing = ref(false)

let startX = 0
let startY = 0
let axis: 'none' | 'x' | 'y' = 'none'
let pointerId: number | null = null
let didDrag = false // был горизонтальный жест — подавить click
let armed: 'none' | 'next' | 'archive' = 'none'

const soldLike = computed(() => isSoldLike(props.item.status))
const hasNext = computed(() => nextStatus(props.item.status) !== null)
const nextLabel = computed(() => {
  const n = nextStatus(props.item.status)
  return n ? STATUS_LABELS[n] : ''
})

// Цена показывается в базовой валюте склада (сведённая):
// продано -> фактическая продажа; иначе -> цена объявления или себестоимость.
const priceValue = computed<number | null | undefined>(() =>
  soldLike.value
    ? props.item.selling_price_base
    : (props.item.list_price_base ?? props.item.selling_price_base ?? props.item.cost_price_base),
)

/** Прогресс жеста 0..1 к порогу — для плавной подсветки фона-действия. */
const progressRight = computed(() => Math.min(1, Math.max(0, dx.value) / THRESHOLD))
const progressLeft = computed(() => Math.min(1, Math.max(0, -dx.value) / THRESHOLD))

// --- Блокировка скролла страницы во время горизонтального жеста --- //
function blockScroll(e: TouchEvent): void {
  if (axis === 'x') e.preventDefault()
}
function attachScrollLock(): void {
  window.addEventListener('touchmove', blockScroll, { passive: false })
}
function detachScrollLock(): void {
  window.removeEventListener('touchmove', blockScroll)
}

function applyResistance(raw: number): number {
  // До порога — 1:1 за пальцем; после — движение «вязнет».
  const abs = Math.abs(raw)
  if (abs <= THRESHOLD) return raw
  const over = abs - THRESHOLD
  return Math.sign(raw) * (THRESHOLD + over * OVERDRAG)
}

function setArmed(next: 'none' | 'next' | 'archive'): void {
  if (armed !== next) {
    armed = next
    if (next !== 'none') hapticSelection() // тик при пересечении порога
  }
}

function onDown(e: PointerEvent): void {
  if (!props.swipeable || removing.value) return
  startX = e.clientX
  startY = e.clientY
  axis = 'none'
  didDrag = false
  armed = 'none'
  pointerId = e.pointerId
  attachScrollLock()
}

function onMove(e: PointerEvent): void {
  if (pointerId !== e.pointerId) return
  const ddx = e.clientX - startX
  const ddy = e.clientY - startY

  if (axis === 'none') {
    if (Math.abs(ddx) < 10 && Math.abs(ddy) < 10) return
    // Горизонталь должна явно доминировать (в 1.3 раза), иначе отдаём вертикали.
    axis = Math.abs(ddx) > Math.abs(ddy) * 1.3 ? 'x' : 'y'
    if (axis === 'x') {
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
      dragging.value = true
      didDrag = true
    }
  }
  if (axis !== 'x') return

  let val = applyResistance(ddx)
  if (val > 0 && !hasNext.value) val = Math.min(val * 0.2, 28) // нет следующего статуса — тугая резина
  if (val < 0) val = Math.max(val, -MAX_LEFT)
  dx.value = val

  if (val > THRESHOLD && hasNext.value) setArmed('next')
  else if (val < -THRESHOLD) setArmed('archive')
  else setArmed('none')
}

function finishGesture(e: PointerEvent): void {
  if (pointerId !== e.pointerId) return
  pointerId = null
  detachScrollLock()
  const wasX = axis === 'x'
  axis = 'none'
  dragging.value = false
  if (!wasX) {
    dx.value = 0
    return
  }
  const val = dx.value
  if (val > THRESHOLD && hasNext.value) {
    dx.value = 0 // плавно пружинит, бейдж статуса обновится рядом
    emit('next')
  } else if (val < -THRESHOLD) {
    removing.value = true
    dx.value = -Math.max(320, (e.currentTarget as HTMLElement).offsetWidth)
    window.setTimeout(() => emit('archive'), 190)
  } else {
    dx.value = 0
  }
}

function onCancel(e: PointerEvent): void {
  if (pointerId !== e.pointerId) return
  pointerId = null
  detachScrollLock()
  axis = 'none'
  dragging.value = false
  if (!removing.value) dx.value = 0
}

function onClick(): void {
  if (!didDrag && Math.abs(dx.value) < 4) emit('open')
}

onBeforeUnmount(detachScrollLock)
</script>

<template>
  <div class="card-wrap">
    <!-- Фоновые действия: прозрачность растёт с прогрессом жеста -->
    <div class="action action-next" :class="{ armed: progressRight >= 1 }" :style="{ opacity: progressRight }">
      <svg viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="m9 6 6 6-6 6" stroke="currentColor" /></svg>
      <span class="action-label">{{ hasNext ? nextLabel : '—' }}</span>
    </div>
    <div class="action action-archive" :class="{ armed: progressLeft >= 1 }" :style="{ opacity: progressLeft }">
      <span class="action-label">В архив</span>
      <svg viewBox="0 0 24 24" width="20" height="20">
        <path fill="currentColor" d="M3 4h18v4H3V4zm2 6h14l-1 10H6L5 10zm4 2v6h2v-6H9zm4 0v6h2v-6h-2z" />
      </svg>
    </div>

    <!-- Передний план -->
    <div
      class="card"
      :class="{ dragging, removing }"
      :style="{ transform: `translateX(${dx}px)` }"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="finishGesture"
      @pointercancel="onCancel"
      @click="onClick"
    >
      <div class="photo">
        <AuthImage
          :item-id="item.id"
          :index="0"
          :photo-count="item.photo_count"
          :alt="item.title"
          :width="400"
        />
      </div>
      <div class="body">
        <div class="row-top">
          <span class="sku num">{{ item.sku }}</span>
          <StatusBadge :status="item.status" />
        </div>
        <div v-if="generating" class="title-line gen-shimmer">✨ Генерирую название…</div>
        <div v-else class="title-line">{{ item.title }}</div>
        <div v-if="generating" class="cat gen-shimmer gen-small">описание пишется по фото</div>
        <div v-else class="cat hint">
          {{ item.brand }} · {{ item.category }}<span v-if="item.size"> · {{ item.size }}</span>
        </div>
        <div v-if="showFinance" class="price-row">
          <Money :value="priceValue" :colored="soldLike" strong />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.card-wrap {
  position: relative;
  border-radius: var(--radius);
  overflow: hidden;
  background: var(--tg-theme-secondary-bg-color);
}
.action {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 20px;
  font-weight: 700;
  font-size: 13px;
  color: #fff;
  opacity: 0;
}
.action.armed .action-label {
  transform: scale(1.06);
}
.action-label {
  transition: transform 0.12s ease;
}
.action-next {
  justify-content: flex-start;
  background: var(--accent-positive);
}
.action-archive {
  justify-content: flex-end;
  background: var(--tg-theme-destructive-text-color);
}

.card {
  position: relative;
  display: flex;
  gap: 12px;
  padding: 12px;
  background: var(--tg-theme-bg-color);
  touch-action: pan-y;
  will-change: transform;
  height: 112px;
  user-select: none;
  -webkit-user-select: none;
  /* Плавно по умолчанию; во время активного перетаскивания transition отключаем. */
  transition: transform 0.24s cubic-bezier(0.22, 1, 0.36, 1);
}
.card.dragging {
  transition: none;
}
.photo {
  flex: none;
  width: 88px;
  height: 88px;
  pointer-events: none;
}
.body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  pointer-events: none;
}
.row-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.sku {
  font-size: 12px;
  color: var(--tg-theme-hint-color);
  font-weight: 600;
}
.title-line {
  font-size: 16px;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cat {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.price-row {
  margin-top: auto;
  font-size: 16px;
}
/* Индикатор фоновой AI-генерации: мягкое «дыхание» текста. */
.gen-shimmer {
  color: var(--tg-theme-link-color);
  animation: gen-pulse 1.4s ease-in-out infinite;
}
.gen-small {
  font-size: 13px;
}
@keyframes gen-pulse {
  0%,
  100% {
    opacity: 0.45;
  }
  50% {
    opacity: 1;
  }
}
</style>
