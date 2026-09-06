<script setup lang="ts">
import { ref, watch } from 'vue'
import BottomSheet from '@/shared/ui/BottomSheet.vue'
import StatusBadge from '@/shared/ui/StatusBadge.vue'
import AutocompleteInput from '@/components/AutocompleteInput.vue'
import type { ItemStatus } from '@/shared/api/types'
import { ALL_STATUSES } from '@/shared/utils/status'

const props = defineProps<{
  modelValue: boolean
  status: ItemStatus | null
  brand: string | null
  category: string | null
}>()

const emit = defineEmits<{
  'update:modelValue': [boolean]
  apply: [{ status: ItemStatus | null; brand: string | null; category: string | null }]
  reset: []
}>()

const localStatus = ref<ItemStatus | null>(props.status)
const localBrand = ref<string>(props.brand ?? '')
const localCategory = ref<string>(props.category ?? '')

watch(
  () => props.modelValue,
  (open) => {
    if (open) {
      localStatus.value = props.status
      localBrand.value = props.brand ?? ''
      localCategory.value = props.category ?? ''
    }
  },
)

function toggleStatus(s: ItemStatus): void {
  localStatus.value = localStatus.value === s ? null : s
}

function apply(): void {
  emit('apply', {
    status: localStatus.value,
    brand: localBrand.value.trim() || null,
    category: localCategory.value.trim() || null,
  })
  emit('update:modelValue', false)
}

function reset(): void {
  localStatus.value = null
  localBrand.value = ''
  localCategory.value = ''
  emit('reset')
  emit('update:modelValue', false)
}
</script>

<template>
  <BottomSheet
    :model-value="modelValue"
    title="Фильтры"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="section-label">Статус</div>
    <div class="chips">
      <button
        v-for="s in ALL_STATUSES"
        :key="s"
        class="chip"
        :class="{ selected: localStatus === s }"
        @click="toggleStatus(s)"
      >
        <StatusBadge :status="s" />
      </button>
    </div>

    <div class="section-label">Бренд</div>
    <AutocompleteInput v-model="localBrand" field="brand" placeholder="Любой бренд" />

    <div class="section-label">Категория</div>
    <AutocompleteInput v-model="localCategory" field="category" placeholder="Любая категория" />

    <div class="actions">
      <button class="btn btn-secondary tap" @click="reset">Сбросить</button>
      <button class="btn btn-primary tap" @click="apply">Применить</button>
    </div>
  </BottomSheet>
</template>

<style scoped>
.section-label {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--tg-theme-hint-color);
  margin: 12px 0 7px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.chip {
  padding: 3.5px;
  border-radius: 7px;
  border: 1px solid transparent;
  background: var(--tg-theme-secondary-bg-color);
}
.chip.selected {
  border-color: var(--tg-theme-link-color);
}
.actions {
  display: flex;
  gap: 8.5px;
  margin-top: 17.5px;
}
.btn {
  flex: 1;
  min-height: var(--tap);
  border-radius: var(--radius);
  font-size: 13px;
  font-weight: 700;
}
.btn-primary {
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
}
.btn-secondary {
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
}
</style>
