<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useVirtualList } from '@vueuse/core'
import { storeToRefs } from 'pinia'
import ItemCard from '@/components/ItemCard.vue'
import FilterSheet from '@/components/FilterSheet.vue'
import SellPriceSheet from '@/components/SellPriceSheet.vue'
import { useItemsStore } from '@/stores/items'
import { useSessionStore } from '@/stores/session'
import type { Currency, ItemOut, ItemStatus } from '@/shared/api/types'
import { nextStatus } from '@/shared/utils/status'
import { hapticImpact, hapticNotify } from '@/shared/telegram/webapp'
import { consumeDrilldown, nav, openCreate, openDetail } from '@/app/navigation'

const items = useItemsStore()
const session = useSessionStore()
const { items: itemList, loading, loadingMore, error, isEmpty } = storeToRefs(items)

const ROW_HEIGHT = 124
const { list, containerProps, wrapperProps } = useVirtualList(itemList, {
  itemHeight: ROW_HEIGHT,
  overscan: 6,
})

// --------------------------- Поиск (debounce 300ms) --------------------------- //
const search = ref('')
let searchTimer: number | undefined
watch(search, (value) => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => void items.setSearch(value.trim()), 300)
})

// --------------------------- Фильтры --------------------------- //
const filterOpen = ref(false)
function applyFilters(payload: {
  status: ItemStatus | null
  brand: string | null
  category: string | null
}): void {
  void items.setFilters({ ...payload, ids: null })
}
function resetFilters(): void {
  search.value = ''
  void items.resetFilters()
}

// --------------------------- Свайп-переходы --------------------------- //
const priceOpen = ref(false)
const priceItem = ref<ItemOut | null>(null)

async function onNext(item: ItemOut): Promise<void> {
  const target = nextStatus(item.status)
  if (!target) return
  hapticImpact('light')
  // При продаже всегда спрашиваем цену/валюту (если ещё не проставлена фактическая).
  if (target === 'SOLD' && (item.selling_price === null || item.selling_price === undefined)) {
    priceItem.value = item
    priceOpen.value = true
    return
  }
  const ok = await items.applyStatus(item, { targetStatus: target })
  if (ok) hapticNotify('success')
}

async function onConfirmPrice(price: number, currency: Currency): Promise<void> {
  const item = priceItem.value
  if (!item) return
  const ok = await items.applyStatus(item, {
    targetStatus: 'SOLD',
    sellingPrice: price,
    sellingCurrency: currency,
  })
  if (ok) hapticNotify('success')
  priceItem.value = null
}

function onArchive(item: ItemOut): void {
  hapticImpact('medium')
  items.archiveWithUndo(item)
}

function onOpen(item: ItemOut): void {
  openDetail(item.id)
}

function toggleArchive(): void {
  hapticImpact('light')
  search.value = ''
  void items.setArchivedView(!items.viewArchived)
}

// --------------------------- Бесконечная подгрузка --------------------------- //
function onScroll(e: Event): void {
  const el = e.target as HTMLElement
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - ROW_HEIGHT * 4) {
    void items.loadMore()
  }
}

// --------------------------- Drill-down из BI --------------------------- //
function consumeAndApply(): void {
  const dd = consumeDrilldown()
  if (!dd) return
  search.value = ''
  void items.setFilters({
    ids: dd.ids ?? null,
    status: dd.status ?? null,
    brand: dd.brand ?? null,
    category: null,
    search: null,
  })
}

watch(
  () => nav.activeTab,
  (tab) => {
    if (tab === 'inventory') consumeAndApply()
  },
)

onMounted(() => {
  consumeAndApply()
  if (itemList.value.length === 0) void items.loadFirst()
})

onBeforeUnmount(() => window.clearTimeout(searchTimer))
</script>

<template>
  <div class="screen">
    <header class="topbar">
      <div class="search-wrap">
        <svg class="search-icon" viewBox="0 0 24 24" width="18" height="18">
          <path
            fill="currentColor"
            d="M10 4a6 6 0 1 0 3.5 10.9l4.3 4.3 1.4-1.4-4.3-4.3A6 6 0 0 0 10 4zm0 2a4 4 0 1 1 0 8 4 4 0 0 1 0-8z"
          />
        </svg>
        <input
          v-model="search"
          class="search-input"
          type="search"
          inputmode="search"
          placeholder="Поиск: SKU, бренд, название"
        />
      </div>
      <button
        class="filter-btn tap"
        :class="{ active: items.viewArchived }"
        :aria-label="items.viewArchived ? 'Показать активные' : 'Показать архив'"
        @click="toggleArchive"
      >
        <svg viewBox="0 0 24 24" width="22" height="22">
          <path fill="currentColor" d="M3 4h18v4H3V4zm2 6h14l-1 10H6L5 10zm4 2v6h6v-2h-4v-4H9z" />
        </svg>
      </button>
      <button
        class="filter-btn tap"
        :class="{ active: items.activeFilterCount > 0 }"
        aria-label="Фильтры"
        @click="filterOpen = true"
      >
        <svg viewBox="0 0 24 24" width="22" height="22">
          <path fill="currentColor" d="M3 5h18v2l-7 7v5l-4 2v-7L3 7V5z" />
        </svg>
        <span v-if="items.activeFilterCount > 0" class="filter-badge num">{{
          items.activeFilterCount
        }}</span>
      </button>
    </header>

    <div v-if="items.viewArchived" class="drill-banner">
      Архив — товары, убранные со склада
      <button class="drill-clear" @click="toggleArchive">К активным</button>
    </div>

    <div v-if="items.filters.ids && items.filters.ids.length" class="drill-banner">
      Показаны зависшие товары ({{ items.filters.ids.length }})
      <button class="drill-clear" @click="resetFilters">Сбросить</button>
    </div>

    <div v-if="loading && itemList.length === 0" class="state hint">Загрузка…</div>
    <div v-else-if="error" class="state">
      <p class="negative">{{ error }}</p>
      <button class="retry" @click="items.loadFirst()">Повторить</button>
    </div>
    <div v-else-if="isEmpty" class="state hint">
      {{ items.viewArchived ? 'Архив пуст.' : 'Пусто. Добавьте первый товар кнопкой «+».' }}
    </div>

    <div v-else v-bind="containerProps" class="list no-scrollbar" @scroll="onScroll">
      <div v-bind="wrapperProps">
        <div v-for="row in list" :key="row.data.id" class="row" :style="{ height: ROW_HEIGHT + 'px' }">
          <ItemCard
            :item="row.data"
            :show-finance="session.canSeeFinance"
            :swipeable="!items.viewArchived"
            :generating="!!items.aiPending[row.data.id]"
            @next="onNext(row.data)"
            @archive="onArchive(row.data)"
            @open="onOpen(row.data)"
          />
        </div>
        <div v-if="loadingMore" class="more hint">Загрузка…</div>
      </div>
    </div>

    <button v-if="!items.viewArchived" class="fab" aria-label="Добавить товар" @click="openCreate">
      <svg viewBox="0 0 24 24" width="28" height="28">
        <path fill="currentColor" d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6V5z" />
      </svg>
    </button>

    <FilterSheet
      v-model="filterOpen"
      :status="items.filters.status ?? null"
      :brand="items.filters.brand ?? null"
      :category="items.filters.category ?? null"
      @apply="applyFilters"
      @reset="resetFilters"
    />
    <SellPriceSheet v-model="priceOpen" :item="priceItem" @confirm="onConfirmPrice" />
  </div>
</template>

<style scoped>
.screen {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: calc(var(--safe-top) + 10px) 12px 10px;
  background: var(--tg-theme-bg-color);
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.search-wrap {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
}
.search-icon {
  position: absolute;
  left: 10px;
  color: var(--tg-theme-hint-color);
  pointer-events: none;
}
.search-input {
  width: 100%;
  min-height: var(--tap);
  padding: 0 12px 0 34px;
  border-radius: var(--radius);
  border: none;
  background: var(--tg-theme-secondary-bg-color);
  outline: none;
}
.filter-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
}
.filter-btn.active {
  color: var(--tg-theme-link-color);
}
.filter-badge {
  position: absolute;
  top: 2px;
  right: 2px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--tg-theme-link-color);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.drill-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 14px;
  font-size: 13px;
  background: color-mix(in srgb, var(--tg-theme-link-color) 12%, transparent);
}
.drill-clear {
  color: var(--tg-theme-link-color);
  font-weight: 700;
  padding: 6px;
}
.list {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}
.row {
  padding-bottom: 12px;
}
.state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 40px 20px;
  text-align: center;
}
.retry,
.more {
  padding: 10px;
  text-align: center;
}
.retry {
  color: var(--tg-theme-link-color);
  font-weight: 700;
}
.fab {
  position: fixed;
  right: 16px;
  bottom: calc(var(--nav-height) + var(--safe-bottom) + 16px);
  width: 56px;
  height: 56px;
  border-radius: var(--radius);
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
  z-index: 40;
}
</style>
