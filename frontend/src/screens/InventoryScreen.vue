<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useVirtualList } from '@vueuse/core'
import { storeToRefs } from 'pinia'
import ItemCard from '@/components/ItemCard.vue'
import FilterSheet from '@/components/FilterSheet.vue'
import SellPriceSheet from '@/components/SellPriceSheet.vue'
import Money from '@/shared/ui/Money.vue'
import BottomSheet from '@/shared/ui/BottomSheet.vue'
import { CURRENCIES, CURRENCY_SYMBOLS } from '@/shared/api/types'
import { fx, isConverted, setDisplay } from '@/shared/utils/currency'
import { useItemsStore } from '@/stores/items'
import { useSessionStore } from '@/stores/session'
import { useAnalyticsStore } from '@/stores/analytics'
import { useToastStore } from '@/stores/toast'
import type { Currency, ItemOut, ItemStatus } from '@/shared/api/types'
import { nextStatus, requiresListPrice, requiresSellingPrice } from '@/shared/utils/status'
import { hapticImpact, hapticNotify, hapticSelection } from '@/shared/telegram/webapp'
import { consumeDrilldown, nav, openCreate, openDetail } from '@/app/navigation'

const items = useItemsStore()
const toast = useToastStore()
const analytics = useAnalyticsStore()

/**
 * Шапка показывает не слоган, а цифру, ради которой сюда заходят. Владельцу
 * и аналитику — заработок, сотруднику — сколько вещей в работе: денег он не
 * видит по роли, и пустая плашка была бы обманом.
 */
const heroLabel = computed(() =>
  session.canSeeFinance ? 'Заработано всего' : 'Вещей в работе',
)
const heroValue = computed(() => analytics.summary?.total_profit ?? null)
const activeCount = computed(() => analytics.summary?.active_count ?? null)
const staleCount = computed(() => analytics.summary?.stale.count ?? 0)
const staleDays = computed(() => analytics.summary?.stale.threshold_days ?? 60)

/**
 * Валюта показа. Ничего не меняет в данных — только переводит уже
 * посчитанные суммы, поэтому переключение мгновенное и без запросов.
 */
const curOpen = ref(false)
function pickCurrency(c: Currency): void {
  hapticSelection()
  setDisplay(c)
  curOpen.value = false
}

/** Тап по «залежалось» — тот же drill-down, что из аналитики. */
function showStale(): void {
  const ids = analytics.summary?.stale.item_ids ?? []
  if (!ids.length) return
  hapticImpact('light')
  void items.setFilters({ ids, status: null, search: null })
}

const session = useSessionStore()
const { items: itemList, loading, loadingMore, error, isEmpty } = storeToRefs(items)

// Высота строки = карточка (118) + зазор (10). Обе величины заданы в CSS —
// в ItemCard и в .row ниже. Расходиться им нельзя: виртуальный список
// считает позиции по этому числу, и при рассинхроне прокрутка поедет.
const ROW_HEIGHT = 128
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

// --------------------------- Смена статуса --------------------------- //
const priceOpen = ref(false)
const priceItem = ref<ItemOut | null>(null)

/**
 * Возврат в мини-апп после чата с ботом. Пока пользователь подтверждал
 * предпросмотр или отвечал в комментариях, данные могли уйти вперёд —
 * подтягиваем их, чтобы не приходилось перезагружать страницу.
 */
function onVisible(): void {
  if (document.visibilityState === 'visible' && nav.overlay === null) {
    void items.refresh()
  }
}

async function onNext(item: ItemOut): Promise<void> {
  const target = nextStatus(item.status)
  if (!target) return
  // Без цены в объявлении в канал не выпускаем — предупреждаем и не двигаем.
  if (requiresListPrice(target) && (item.list_price === null || item.list_price === undefined)) {
    hapticNotify('error')
    toast.error(`${item.brand} ${item.sku}: укажите цену продажи — без неё нельзя выставить`)
    return
  }
  hapticImpact('light')
  // «Отправлен» = продано: спрашиваем цену, если фактическая не проставлена.
  if (requiresSellingPrice(target) && (item.selling_price === null || item.selling_price === undefined)) {
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
    targetStatus: 'SHIPPED',
    sellingPrice: price,
    sellingCurrency: currency,
  })
  if (ok) hapticNotify('success')
  priceItem.value = null
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
  // Сводка для шапки. Тянем один раз: числа меняются медленно, а список
  // должен появиться раньше — поэтому запрос идёт следом, а не блокирует.
  if (analytics.summary === null) void analytics.fetch()
})

document.addEventListener('visibilitychange', onVisible)

onBeforeUnmount(() => {
  window.clearTimeout(searchTimer)
  document.removeEventListener('visibilitychange', onVisible)
})
</script>

<template>
  <div class="screen">
    <header class="head">
      <div class="head-top">
        <span class="store">
          <span class="store-dot" aria-hidden="true" />
          {{ session.currentStore?.name ?? 'Склад' }}
        </span>
        <button
          v-if="session.canSeeFinance"
          class="cur-btn"
          :class="{ on: isConverted() }"
          :aria-label="`Валюта показа: ${fx.display}`"
          @click="curOpen = true"
        >
          {{ CURRENCY_SYMBOLS[fx.display] }}
        </button>
        <button
          class="icon-btn"
          :class="{ on: items.viewArchived }"
          :aria-label="items.viewArchived ? 'Показать активные' : 'Показать архив'"
          @click="toggleArchive"
        >
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
               stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 7h18v4H3zM5 11v9h14v-9M10 15h4" />
          </svg>
        </button>
      </div>

      <p class="hero-label">{{ heroLabel }}</p>
      <p class="hero-value">
        <Money v-if="session.canSeeFinance && heroValue !== null" :value="heroValue" strong />
        <template v-else-if="!session.canSeeFinance">{{ activeCount ?? '—' }}</template>
        <template v-else>—</template>
      </p>
      <p v-if="session.canSeeFinance" class="hero-sub">
        <span v-if="activeCount !== null">В работе <b>{{ activeCount }}</b></span>
        <span v-if="isConverted()" class="conv">по курсу к {{ fx.base }}</span>
      </p>

      <div class="find">
        <div class="field">
          <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor"
               stroke-width="2" stroke-linecap="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7" /><path d="m20 20-3.6-3.6" />
          </svg>
          <input
            v-model="search"
            class="field-input"
            type="search"
            inputmode="search"
            placeholder="Поиск по складу"
          />
        </div>
        <button
          class="icon-btn filter"
          :class="{ on: items.activeFilterCount > 0 }"
          aria-label="Фильтры"
          @click="filterOpen = true"
        >
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
               stroke-width="1.9" stroke-linecap="round" aria-hidden="true">
            <path d="M4 6h16M7 12h10M10 18h4" />
          </svg>
          <span v-if="items.activeFilterCount > 0" class="dot num">{{ items.activeFilterCount }}</span>
        </button>
        <button v-if="!items.viewArchived" class="add" aria-label="Добавить вещь" @click="openCreate">
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor"
               stroke-width="2.3" stroke-linecap="round" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </div>

      <button v-if="staleCount > 0 && !items.viewArchived" class="stale" @click="showStale">
        <span class="stale-n">{{ staleCount }}</span>
        <span class="stale-t">{{ staleCount === 1 ? 'вещь лежит' : 'вещей лежат' }}
          дольше {{ staleDays }} дней</span>
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
             stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="m9 6 6 6-6 6" />
        </svg>
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
        <div
          v-for="row in list"
          :key="row.data.id"
          class="row"
          :style="{ height: ROW_HEIGHT + 'px' }"
        >
          <ItemCard
            :item="row.data"
            :show-finance="session.canSeeFinance"
            :actionable="!items.viewArchived"
            :generating="!!items.aiPending[row.data.id]"
            @next="onNext(row.data)"
            @open="onOpen(row.data)"
          />
        </div>
        <div v-if="loadingMore" class="more hint">Загрузка…</div>
      </div>
    </div>


    <FilterSheet
      v-model="filterOpen"
      :status="items.filters.status ?? null"
      :brand="items.filters.brand ?? null"
      :category="items.filters.category ?? null"
      @apply="applyFilters"
      @reset="resetFilters"
    />
    <BottomSheet v-model="curOpen" title="Валюта показа">
      <p class="cur-note">
        Меняется только вид: суммы склада остаются в {{ fx.base }}, здесь их
        переводят по курсу Нацбанка.
      </p>
      <div class="cur-list">
        <button
          v-for="c in CURRENCIES"
          :key="c"
          class="cur-row"
          :class="{ sel: c === fx.display }"
          @click="pickCurrency(c)"
        >
          <span class="cur-sym">{{ CURRENCY_SYMBOLS[c] }}</span>
          <span class="cur-code">{{ c }}</span>
          <span v-if="c === fx.base" class="cur-base">базовая</span>
          <svg
            v-if="c === fx.display"
            viewBox="0 0 24 24"
            width="18"
            height="18"
            fill="none"
            stroke="currentColor"
            stroke-width="2.4"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="m5 13 4 4L19 7" />
          </svg>
        </button>
      </div>
    </BottomSheet>

    <SellPriceSheet v-model="priceOpen" :item="priceItem" @confirm="onConfirmPrice" />
  </div>
</template>

<style scoped>
.screen {
  display: flex;
  flex-direction: column;
  height: 100%;
}
/* Шапка: сначала цифра, ради которой открывают экран, потом поиск.
   Референс держал тут слоган — у склада слоган не нужен, нужны деньги. */
.head {
  padding: calc(var(--safe-top) + 10px) var(--pad) 4px;
  flex: none;
}
.head-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 16px;
}
.store {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  max-width: 70%;
  padding: 7px 13px 7px 10px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.store-dot {
  flex: none;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--s-ship);
}
.icon-btn {
  position: relative;
  flex: none;
  width: 44px;
  height: 44px;
  border-radius: var(--r-field);
  background: var(--ink-1);
  color: var(--fg-1);
  display: grid;
  place-items: center;
}
.icon-btn.on {
  background: var(--ink-3);
  color: var(--fg-0);
}
.head-top .icon-btn {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: var(--ink-2);
}
.dot {
  position: absolute;
  top: 5px;
  right: 5px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 8px;
  background: var(--brand);
  color: var(--brand-ink);
  font-size: 10px;
  font-weight: 700;
  line-height: 16px;
  text-align: center;
}
.hero-label {
  margin: 0 0 6px;
  font-size: 13px;
  color: var(--fg-1);
}
.hero-value {
  margin: 0;
  font-size: 40px;
  line-height: 42px;
  font-weight: 700;
  letter-spacing: -0.035em;
  font-variant-numeric: tabular-nums;
}
.hero-sub {
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--fg-1);
}
.hero-sub b {
  color: var(--fg-0);
  font-weight: 650;
}
.conv {
  margin-left: 12px;
  color: var(--fg-2);
}
.cur-btn {
  flex: none;
  min-width: 38px;
  height: 38px;
  padding: 0 12px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
  color: var(--fg-1);
  font-size: 15px;
  font-weight: 700;
}
.cur-btn.on {
  background: color-mix(in srgb, var(--brand) 18%, transparent);
  color: var(--brand);
}
.cur-note {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 18px;
  color: var(--fg-1);
}
.cur-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cur-row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  height: 54px;
  padding: 0 14px;
  border-radius: var(--r-field);
  background: var(--ink-1);
  color: var(--fg-0);
  text-align: left;
}
.cur-row.sel {
  background: var(--ink-3);
  color: var(--brand);
}
.cur-sym {
  width: 26px;
  font-size: 18px;
  font-weight: 700;
}
.cur-code {
  flex: 1;
  font-size: 15px;
  font-weight: 650;
  color: var(--fg-0);
}
.cur-base {
  font-size: 12px;
  color: var(--fg-2);
}
.find {
  display: flex;
  gap: 10px;
  margin: 18px 0 0;
}
.field {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 50px;
  padding: 0 14px;
  border-radius: var(--r-field);
  background: var(--ink-1);
  color: var(--fg-2);
}
.field-input {
  flex: 1;
  min-width: 0;
  border: 0;
  background: none;
  color: var(--fg-0);
  /* 16px обязателен: на меньшем iOS зумит страницу при фокусе. */
  font-size: 16px;
}
.field-input::placeholder {
  color: var(--fg-2);
}
.field-input::-webkit-search-cancel-button {
  -webkit-appearance: none;
}
.find .icon-btn {
  height: 50px;
  width: 50px;
}
.add {
  flex: none;
  width: 50px;
  height: 50px;
  border-radius: var(--r-field);
  background: var(--brand);
  color: var(--brand-ink);
  display: grid;
  place-items: center;
}
.add:active {
  opacity: 0.85;
}
/* Полоса «залежалось»: не украшение, а единственная подсказка, что делать. */
.stale {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  margin-top: 12px;
  padding: 11px 12px;
  border-radius: var(--r-field);
  background: color-mix(in srgb, var(--s-prep) 13%, transparent);
  color: var(--fg-0);
  text-align: left;
}
.stale-n {
  flex: none;
  font-size: 17px;
  font-weight: 700;
  color: var(--s-prep);
  font-variant-numeric: tabular-nums;
}
.stale-t {
  flex: 1;
  font-size: 13px;
  line-height: 17px;
  color: var(--fg-1);
}
.stale svg {
  flex: none;
  color: var(--fg-2);
}
.list {
  flex: 1;
  overflow-y: auto;
  /* Поля совпадают с шапкой: карточки и герой стоят на одной вертикали. */
  padding: 8px var(--pad) calc(var(--nav-height) + var(--safe-bottom) + 12px);
}
.row {
  padding-bottom: 10px;
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
</style>
