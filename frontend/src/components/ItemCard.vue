<script setup lang="ts">
/**
 * Строка товара в списке.
 *
 * Свайпов здесь нет намеренно. Жест был неочевиден (о нём нужно догадаться),
 * конфликтовал с вертикальной прокруткой и срабатывал вхолостую при быстром
 * пролистывании. Вместо него — явная кнопка перехода в следующий статус.
 *
 * В кнопке только целевой статус, без текущего: текущий уже написан бейджем
 * справа сверху, и полная надпись «Сфотографирован → Выставлен» не влезала
 * в строку рядом с ценой — обрезалась многоточием. Кнопка повторяет форму
 * бейджа, отличаясь только цветом, поэтому пара читается как «сейчас — и
 * куда дальше», а не как два разных элемента.
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

/**
 * Бренд у вещи может быть не заполнен, и шаблон «бренд · категория · размер»
 * начинался с висящей точки: « · Лонгслив · S». Собираем из непустых частей.
 */
const subtitle = computed(() =>
  [props.item.brand, props.item.category, props.item.size]
    .map((x) => (x ?? '').trim())
    .filter(Boolean)
    .join(' · '),
)

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
      <div v-else class="cat hint">{{ subtitle }}</div>

      <div class="bottom-row">
        <button
          v-if="showStep"
          class="step tap"
          :aria-label="`Перевести из «${STATUS_LABELS[item.status]}» в «${nextLabel}»`"
          @click="onStep"
        >
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
</template>

<style scoped>
/*
 * Строка — серая плашка, как блоки в настройках и админке. Раньше фон
 * карточки совпадал с фоном экрана, и список читался сплошным полотном.
 *
 * Высота карточки фиксирована: её знает ROW_HEIGHT виртуального списка в
 * InventoryScreen (карточка + зазор строки). Поэтому высота КАЖДОЙ строки
 * внутри задана явно, а сами строки не сжимаются (flex: none).
 *
 * Без этого вёрстка держалась на метриках шрифта: в макете на Linux всё
 * помещалось, а на iPhone с SF Pro строки оказались выше, сумма превысила
 * доступную высоту, и flex сжал текст — название обрезалось сверху и снизу
 * и наезжало на категорию.
 */
.card {
  position: relative;
  display: flex;
  gap: 10px;
  padding: 12px 10px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  height: 120px;
  user-select: none;
  -webkit-user-select: none;
}
.photo {
  flex: none;
  width: 80px;
  height: 96px; /* вся высота содержимого: 120 − 2×12 */
  pointer-events: none;
}
.body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  overflow: hidden;
}
.row-top {
  flex: none;
  height: 20px;
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
  flex: none;
  height: 20px;
  font-size: 16px;
  line-height: 20px;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
}
.cat {
  flex: none;
  height: 16px;
  font-size: 13px;
  line-height: 16px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  pointer-events: none;
}

.bottom-row {
  flex: none;
  height: 22px;
  margin-top: auto; /* остаток высоты уходит сюда, а не в сжатие текста */
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}
/*
 * Повторяет геометрию StatusBadge (шрифт, скругление, отступы, заливка),
 * чтобы бейдж статуса и кнопка перехода выглядели одной парой.
 */
.step {
  /* Сжимается первой: на узком экране обрезать название статуса не жалко,
     а цену пользователь должен видеть целиком. */
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  color: var(--tg-theme-link-color);
  /* К фону темы, а не к прозрачности: на серой плашке альфа-заливка
     сливается с подложкой и чип теряет очертания. */
  background: color-mix(in srgb, var(--tg-theme-link-color) 18%, var(--tg-theme-bg-color));
}
.step:active {
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
}
.step-arrow {
  flex: none;
  opacity: 0.75;
}
.step-next {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.step-empty {
  flex: 1;
}
.price-row {
  flex: none;
  font-size: 16px;
  line-height: 20px;
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
