<script setup lang="ts">
/**
 * Строка товара в списке.
 *
 * Свайпов здесь нет намеренно. Жест был неочевиден (о нём нужно догадаться),
 * конфликтовал с вертикальной прокруткой и срабатывал вхолостую при быстром
 * пролистывании. Вместо него — явная кнопка перехода в следующий статус:
 * видно, что произойдёт, до нажатия, и промахнуться мимо неё нельзя.
 *
 * Архивация уехала в карточку товара: в строке ей не место, если жестов нет,
 * а второй кнопкой рядом со статусом легко попасть по ошибке.
 */
import { computed } from 'vue'
import type { ItemOut } from '@/shared/api/types'
import AuthImage from '@/shared/ui/AuthImage.vue'
import StatusBadge from '@/shared/ui/StatusBadge.vue'
import Money from '@/shared/ui/Money.vue'
import { isSoldLike, nextStatus, STATUS_LABELS } from '@/shared/utils/status'

const props = withDefaults(
  defineProps<{
    item: ItemOut
    showFinance: boolean
    /** В архиве статусы не двигаем — кнопку прячем. */
    actionable?: boolean
    /** Идёт фоновая AI-генерация названия/описания. */
    generating?: boolean
  }>(),
  { actionable: true, generating: false },
)
const emit = defineEmits<{ next: []; open: [] }>()

const soldLike = computed(() => isSoldLike(props.item.status))
const next = computed(() => nextStatus(props.item.status))
const nextLabel = computed(() => (next.value ? STATUS_LABELS[next.value] : ''))
const showStep = computed(() => props.actionable && next.value !== null)

// Цена показывается в базовой валюте склада (сведённая):
// продано -> фактическая продажа; иначе -> цена объявления или себестоимость.
const priceValue = computed<number | null | undefined>(() =>
  soldLike.value
    ? props.item.selling_price_base
    : (props.item.list_price_base ?? props.item.selling_price_base ?? props.item.cost_price_base),
)

/** Цена до скидки: показываем зачёркнутой рядом с новой. */
const oldPriceValue = computed<number | null>(() =>
  !soldLike.value && props.item.price_before_discount != null
    ? props.item.price_before_discount
    : null,
)

/**
 * Нажатие на кнопку не должно открывать карточку: цель разная, а кнопка
 * лежит внутри кликабельной строки.
 */
function onStep(e: Event): void {
  e.stopPropagation()
  emit('next')
}
</script>

<template>
  <div class="card-wrap">
    <div class="card" @click="emit('open')">
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

        <div class="bottom-row">
          <button
            v-if="showStep"
            class="step tap"
            :aria-label="`Перевести в «${nextLabel}»`"
            @click="onStep"
          >
            <span class="step-now">{{ STATUS_LABELS[item.status] }}</span>
            <span class="step-arrow" aria-hidden="true">→</span>
            <span class="step-next">{{ nextLabel }}</span>
          </button>
          <span v-else class="step-empty"></span>

          <span v-if="showFinance" class="price-row">
            <Money v-if="oldPriceValue !== null" :value="oldPriceValue" class="was" />
            <Money :value="priceValue" :colored="soldLike" strong />
          </span>
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
.card {
  position: relative;
  display: flex;
  gap: 12px;
  padding: 12px;
  background: var(--tg-theme-bg-color);
  height: 112px;
  user-select: none;
  -webkit-user-select: none;
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
}
.row-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  pointer-events: none;
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
  pointer-events: none;
}
.cat {
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
}

.bottom-row {
  margin-top: auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}
/*
 * Кнопка узкая по содержимому и с увеличенной областью нажатия по вертикали:
 * строка невысокая, а палец на телефоне толще подписи.
 */
.step {
  flex: 0 1 auto;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 9px;
  margin: -3px 0;
  border-radius: 999px;
  background: var(--tg-theme-secondary-bg-color);
  font-size: 11px;
  font-weight: 700;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
}
.step:active {
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
}
.step-now {
  color: var(--tg-theme-hint-color);
  overflow: hidden;
  text-overflow: ellipsis;
}
.step:active .step-now {
  color: inherit;
  opacity: 0.75;
}
.step-arrow {
  color: var(--tg-theme-hint-color);
  flex: none;
}
.step:active .step-arrow {
  color: inherit;
}
.step-next {
  color: var(--tg-theme-link-color);
  flex: none;
}
.step:active .step-next {
  color: inherit;
}
.step-empty {
  flex: 1;
}
.price-row {
  flex: none;
  font-size: 16px;
  pointer-events: none;
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
.was {
  text-decoration: line-through;
  color: var(--tg-theme-hint-color);
  margin-right: 6px;
  font-size: 12px;
}
</style>
