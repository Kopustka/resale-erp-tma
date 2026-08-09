<script setup lang="ts">
import { computed } from 'vue'
import { formatMoney, formatMoneySigned } from '@/shared/utils/format'
import type { Currency } from '@/shared/api/types'

const props = withDefaults(
  defineProps<{
    value: number | null | undefined
    /** Валюта суммы; не указана — базовая валюта склада. */
    currency?: Currency | null
    /** Показывать знак «+/-» (для прибыли). */
    signed?: boolean
    /** Подкрашивать зелёным/красным по знаку. */
    colored?: boolean
    strong?: boolean
  }>(),
  { signed: false, colored: false, strong: false, currency: null },
)

const text = computed(() =>
  props.signed
    ? formatMoneySigned(props.value, props.currency)
    : formatMoney(props.value, props.currency),
)

const colorClass = computed(() => {
  if (!props.colored || props.value === null || props.value === undefined) return ''
  return props.value >= 0 ? 'positive' : 'negative'
})
</script>

<template>
  <span :class="[strong ? 'num-strong' : 'num', colorClass]">{{ text }}</span>
</template>
