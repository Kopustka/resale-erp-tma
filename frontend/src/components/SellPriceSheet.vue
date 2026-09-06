<script setup lang="ts">
/** Ввод цены продажи перед переходом в SOLD. */
import { ref, watch } from 'vue'
import BottomSheet from '@/shared/ui/BottomSheet.vue'
import { CURRENCIES, type Currency, type ItemOut } from '@/shared/api/types'
import { useSessionStore } from '@/stores/session'

const props = defineProps<{ modelValue: boolean; item: ItemOut | null }>()
const emit = defineEmits<{
  'update:modelValue': [boolean]
  confirm: [number, Currency]
}>()

const session = useSessionStore()
const raw = ref('')
const currency = ref<Currency>('BYN')

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      raw.value = props.item?.list_price ? String(props.item.list_price) : ''
      currency.value = props.item?.price_currency ?? session.baseCurrency
    }
  },
)

function parsePrice(): number | null {
  const normalized = raw.value.replace(/\s/g, '').replace(',', '.')
  const value = Number(normalized)
  if (!Number.isFinite(value) || value <= 0) return null
  return value
}

function confirm(): void {
  const price = parsePrice()
  if (price === null) return
  emit('confirm', price, currency.value)
  emit('update:modelValue', false)
}
</script>

<template>
  <BottomSheet
    :model-value="modelValue"
    title="Цена продажи"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="sub hint" v-if="item">
      {{ item.sku }} · {{ item.brand }} — отметить как «Продан»
    </p>
    <div class="price-field">
      <input
        v-model="raw"
        class="price-input num-strong"
        inputmode="decimal"
        placeholder="0"
        autofocus
      />
      <select v-model="currency" class="cur-select">
        <option v-for="c in CURRENCIES" :key="c" :value="c">{{ c }}</option>
      </select>
    </div>
    <button class="btn btn-primary tap" :disabled="parsePrice() === null" @click="confirm">
      Продано
    </button>
  </BottomSheet>
</template>

<style scoped>
.sub {
  margin: 0 0 12px;
  font-size: 12px;
}
.price-field {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  margin-bottom: 15.5px;
}
.price-input {
  flex: 1;
  min-width: 0;
  border: none;
  background: transparent;
  outline: none;
  font-size: 24.5px;
  padding: 7px 0;
}
.cur-select {
  font-size: 15.5px;
  font-weight: 700;
  color: var(--tg-theme-text-color);
  background: transparent;
  border: none;
  outline: none;
}
.btn {
  width: 100%;
  min-height: var(--tap);
  border-radius: var(--radius);
  font-size: 14px;
  font-weight: 700;
}
.btn-primary {
  background: var(--accent-positive);
  color: #fff;
}
.btn-primary:disabled {
  opacity: 0.5;
}
</style>
