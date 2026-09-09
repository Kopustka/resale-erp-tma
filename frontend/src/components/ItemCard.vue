<script setup lang="ts">
/**
 * Строка товара в списке.
 *
 * Фотография здесь — главный актив, а не иллюстрация: вещь узнают по снимку
 * раньше, чем прочитают название. Поэтому кадр вертикальный, во всю высоту
 * строки, а текст и цена выстроены рядом столбцом.
 *
 * Цветная полоса под фото повторяет цвет этапа. Она нужна, чтобы список
 * читался боковым зрением при прокрутке: глаз ловит цвет, а не слово.
 *
 * Свайпов нет намеренно — жест был неочевиден, конфликтовал с прокруткой и
 * срабатывал вхолостую. Переход в следующий этап делает явная кнопка.
 *
 * Высота строки фиксирована: её знает ROW_HEIGHT виртуального списка в
 * InventoryScreen. Поэтому каждая строка внутри тоже имеет заданную высоту,
 * а название обрезается в одну строку вместо переноса.
 */
import { computed, ref, watch } from 'vue'
import type { ItemOut } from '@/shared/api/types'
import AuthImage from '@/shared/ui/AuthImage.vue'
import Money from '@/shared/ui/Money.vue'
import {
  ACTION_LABELS,
  isSoldLike,
  nextStatus,
  STATUS_LABELS,
  STATUS_VARS,
} from '@/shared/utils/status'

const props = withDefaults(
  defineProps<{
    item: ItemOut
    showFinance: boolean
    /** В архиве этапы не двигаем — кнопку прячем. */
    actionable?: boolean
    /** Идёт фоновая генерация названия. */
    generating?: boolean
  }>(),
  { actionable: true, generating: false },
)
const emit = defineEmits<{ next: []; open: [] }>()

const soldLike = computed(() => isSoldLike(props.item.status))
const next = computed(() => nextStatus(props.item.status))
/** На кнопке — действие, на бейдже — состояние. */
const nextAction = computed(() => (next.value ? ACTION_LABELS[next.value] : ''))
const nextLabel = computed(() => (next.value ? STATUS_LABELS[next.value] : ''))
const showStep = computed(() => props.actionable && next.value !== null)
const statusVar = computed(() => STATUS_VARS[props.item.status])
const statusLabel = computed(() => STATUS_LABELS[props.item.status])

/** Бренд, категория и размер одной строкой, без висящих разделителей. */
const subtitle = computed(() =>
  [props.item.brand, props.item.category, props.item.size]
    .map((x) => (x ?? '').trim())
    .filter(Boolean)
    .join(' · '),
)

const priceValue = computed<number | null | undefined>(() =>
  soldLike.value
    ? props.item.selling_price_base
    : (props.item.list_price_base ?? props.item.selling_price_base ?? props.item.cost_price_base),
)

const oldPriceValue = computed<number | null>(() =>
  !soldLike.value && props.item.price_before_discount != null
    ? props.item.price_before_discount
    : null,
)

/**
 * Короткая вспышка чипа на каждой смене этапа.
 *
 * При быстрых нажатиях подряд статус успевал смениться несколько раз, но
 * глаз не замечал перехода: текст просто оказывался другим. Вспышка даёт
 * отклик на каждый шаг — видно, что нажатие засчитано, даже если следующее
 * пришло через сто миллисекунд.
 */
const bump = ref(0)
watch(
  () => props.item.status,
  () => {
    bump.value += 1
  },
)

/** Кнопка лежит внутри кликабельной строки — цели разные, всплытие гасим. */
function onStep(e: Event): void {
  e.stopPropagation()
  emit('next')
}
</script>

<template>
  <article
    class="card"
    :style="{ '--s': `var(--s-${statusVar})`, '--s-ink': `var(--s-${statusVar}-ink)` }"
    @click="emit('open')"
  >
    <div class="shot">
      <AuthImage
        :item-id="item.id"
        :index="0"
        :photo-count="item.photo_count"
        :alt="item.title"
        :width="400"
      />
      <span class="spine" aria-hidden="true" />
    </div>

    <div class="body">
      <div class="head">
        <span class="sku">{{ item.sku }}</span>
        <span :key="bump" class="chip">{{ statusLabel }}</span>
      </div>

      <p v-if="generating" class="name gen">Генерирую название…</p>
      <p v-else class="name">{{ item.title }}</p>
      <p class="sub">{{ generating ? 'описание пишется по фото' : subtitle }}</p>

      <div class="foot">
        <span v-if="showFinance" class="price" :class="{ sold: soldLike }">
          <Money v-if="oldPriceValue !== null" :value="oldPriceValue" class="was" />
          <Money :value="priceValue" strong />
        </span>
        <span v-else />

        <button
          v-if="showStep"
          class="step hit"
          :aria-label="`Перевести из «${statusLabel}» в «${nextLabel}»`"
          @click="onStep"
        >
          <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor"
               stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M5 12h12M12 6l6 6-6 6" />
          </svg>
          <span class="step-t">{{ nextAction }}</span>
        </button>
      </div>
    </div>
  </article>
</template>

<style scoped>
.card {
  display: flex;
  gap: 10.5px;
  height: 102px;
  padding: 9.5px;
  border-radius: var(--r-card);
  background: var(--ink-1);
  user-select: none;
  -webkit-user-select: none;
}
.shot {
  position: relative;
  flex: none;
  width: 75px;
  border-radius: 11.5px;
  overflow: hidden;
  background: var(--ink-2);
  pointer-events: none;
}
.shot :deep(img) {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
/* Полоса цвета этапа: список читается боковым зрением при прокрутке. */
.spine {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 3.5px;
  background: var(--s);
}
.body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 7px;
  height: 15.5px;
  pointer-events: none;
}
.sku {
  font-size: 10px;
  font-weight: 650;
  color: var(--fg-2);
  font-variant-numeric: tabular-nums;
}
/* :key меняется на каждом переходе — элемент пересоздаётся, и анимация
   запускается заново. Без этого при частых нажатиях она бы не повторялась. */
.chip {
  animation: chip-in 0.22s cubic-bezier(0.22, 1, 0.36, 1);
  flex: none;
  font-size: 9px;
  font-weight: 700;
  line-height: 1;
  padding: 3.5px 7px;
  border-radius: var(--r-pill);
  white-space: nowrap;
  /* Надпись отдельным токеном: сам цвет этапа на своей заливке не читается. */
  color: var(--s-ink);
  background: color-mix(in srgb, var(--s) 15%, transparent);
}
.name {
  height: 17.5px;
  margin: 4.5px 0 2px;
  font-size: 14px;
  line-height: 17.5px;
  font-weight: 650;
  letter-spacing: -0.012em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
}
.sub {
  height: 14px;
  margin: 0;
  font-size: 11px;
  line-height: 14px;
  color: var(--fg-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
}
.gen {
  color: var(--brand);
  animation: gen-pulse 1.4s ease-in-out infinite;
}
@keyframes chip-in {
  from {
    opacity: 0.2;
    transform: translate3d(0, -3px, 0) scale(0.94);
  }
}
@keyframes gen-pulse {
  0%,
  100% {
    opacity: 0.5;
  }
  50% {
    opacity: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  .gen,
  .chip {
    animation: none;
  }
}
.foot {
  margin-top: auto;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8.5px;
}
.price {
  display: flex;
  align-items: baseline;
  gap: 5px;
  font-size: 17.5px;
  font-weight: 700;
  letter-spacing: -0.025em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  pointer-events: none;
}
.price.sold {
  color: var(--s-ship);
}
.was {
  font-size: 10px;
  font-weight: 500;
  color: var(--fg-2);
  text-decoration: line-through;
  letter-spacing: 0;
}
/* Сжимается первой: обрезать название этапа не жалко, цену — нельзя. */
.step {
  flex: 0 1 auto;
  min-width: 0;
  max-width: 56%;
  display: inline-flex;
  align-items: center;
  gap: 4.5px;
  height: 26px;
  padding: 0 10.5px;
  border-radius: var(--r-pill);
  background: var(--ink-3);
  color: var(--fg-0);
  font-size: 10.5px;
  font-weight: 650;
  white-space: nowrap;
  overflow: hidden;
}
.step svg {
  flex: none;
  opacity: 0.55;
}
.step-t {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.step:active {
  background: var(--brand);
  color: var(--brand-ink);
}
.step:active svg {
  opacity: 0.8;
}
</style>
