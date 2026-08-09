<script setup lang="ts">
/** Поле ввода с автодополнением через /items/suggest/{field}. Debounce 250ms. */
import { onBeforeUnmount, ref } from 'vue'
import { itemsApi } from '@/shared/api/endpoints'

const props = withDefaults(
  defineProps<{
    modelValue: string
    field: 'brand' | 'category'
    placeholder?: string
    inputmode?: 'text' | 'search' | 'decimal' | 'numeric' | 'url' | 'tel' | 'email' | 'none'
  }>(),
  { placeholder: '', inputmode: 'text' },
)
const emit = defineEmits<{ 'update:modelValue': [string] }>()

const suggestions = ref<string[]>([])
const open = ref(false)
let debounceTimer: number | undefined
let controller: AbortController | null = null

function onInput(e: Event): void {
  const value = (e.target as HTMLInputElement).value
  emit('update:modelValue', value)
  scheduleFetch(value)
}

function scheduleFetch(q: string): void {
  window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => void fetchSuggestions(q), 250)
}

async function fetchSuggestions(q: string): Promise<void> {
  controller?.abort()
  controller = new AbortController()
  try {
    const list = await itemsApi.suggest(props.field, q, controller.signal)
    suggestions.value = list.slice(0, 8)
    open.value = suggestions.value.length > 0
  } catch {
    suggestions.value = []
    open.value = false
  }
}

function pick(value: string): void {
  emit('update:modelValue', value)
  open.value = false
}

function onFocus(): void {
  void fetchSuggestions(props.modelValue)
}

function onBlur(): void {
  // Задержка, чтобы успел сработать click по подсказке.
  window.setTimeout(() => (open.value = false), 150)
}

onBeforeUnmount(() => {
  window.clearTimeout(debounceTimer)
  controller?.abort()
})
</script>

<template>
  <div class="ac">
    <input
      class="field"
      :value="modelValue"
      :placeholder="placeholder"
      :inputmode="inputmode"
      autocomplete="off"
      @input="onInput"
      @focus="onFocus"
      @blur="onBlur"
    />
    <ul v-if="open" class="ac-list no-scrollbar">
      <li v-for="s in suggestions" :key="s" class="ac-item" @mousedown.prevent="pick(s)">
        {{ s }}
      </li>
    </ul>
  </div>
</template>

<style scoped>
.ac {
  position: relative;
}
.field {
  width: 100%;
  min-height: var(--tap);
  padding: 0 12px;
  border-radius: var(--radius);
  border: 1px solid var(--tg-theme-secondary-bg-color);
  background: var(--tg-theme-secondary-bg-color);
  outline: none;
}
.field:focus {
  border-color: var(--tg-theme-link-color);
}
.ac-list {
  position: absolute;
  z-index: 20;
  left: 0;
  right: 0;
  top: calc(100% + 4px);
  margin: 0;
  padding: 4px;
  list-style: none;
  max-height: 220px;
  overflow-y: auto;
  background: var(--tg-theme-bg-color);
  border: 1px solid var(--tg-theme-secondary-bg-color);
  border-radius: var(--radius);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.12);
}
.ac-item {
  padding: 10px 10px;
  border-radius: var(--radius-sm);
  font-size: 15px;
}
.ac-item:active {
  background: var(--tg-theme-secondary-bg-color);
}
</style>
