<script setup lang="ts">
import { computed } from 'vue'
import type { ItemStatus } from '@/shared/api/types'
import { STATUS_COLORS, STATUS_LABELS } from '@/shared/utils/status'

const props = defineProps<{ status: ItemStatus }>()

const color = computed(() => STATUS_COLORS[props.status])
const label = computed(() => STATUS_LABELS[props.status])
</script>

<template>
  <span class="badge" :style="{ '--c': color }">
    <span class="dot" />
    {{ label }}
  </span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 4.5px;
  font-size: 10.5px;
  font-weight: 600;
  line-height: 1;
  padding: 3.5px 7px;
  border-radius: 5px;
  color: var(--c);
  /* Плоский фон-акцент: цвет статуса, подмешанный к фону темы. На белом
     выглядит как раньше, но на серой плашке списка не мутнеет. */
  background: color-mix(in srgb, var(--c) 18%, var(--tg-theme-bg-color));
  white-space: nowrap;
}
.dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--c);
  flex: none;
}
</style>
