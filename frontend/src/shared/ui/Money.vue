<script setup lang="ts">
import { computed } from 'vue'
import { formatMoney, formatMoneySigned } from '@/shared/utils/format'
import { fromBase } from '@/shared/utils/currency'
import type { Currency } from '@/shared/api/types'

const props = withDefaults(
  defineProps<{
    value: number | null | undefined
    /**
     * Валюта суммы. Не указана — сумма в базовой валюте склада, и её
     * переводим в валюту отображения. Указана — показываем как есть: это
     * цифра, которую человек сам ввёл, и переводить её было бы враньём.
     */
    currency?: Currency | null
    /** Показывать знак «+/-» (для прибыли). */
    signed?: boolean
    /** Подкрашивать зелёным/красным по знаку. */
    colored?: boolean
    strong?: boolean
  }>(),
  { signed: false, colored: false, strong: false, currency: null },
)

const shown = computed(() => {
  if (props.value === null || props.value === undefined) return props.value
  return props.currency ? props.value : fromBase(props.value)
})

const text = computed(() =>
  props.signed
    ? formatMoneySigned(shown.value, props.currency)
    : formatMoney(shown.value, props.currency),
)

const colorClass = computed(() => {
  if (!props.colored || props.value === null || props.value === undefined) return ''
  return props.value >= 0 ? 'positive' : 'negative'
})
</script>

<template>
  <span :class="[strong ? 'num-strong' : 'num', colorClass]">{{ text }}</span>
</template>
