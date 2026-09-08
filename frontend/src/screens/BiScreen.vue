<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useAnalyticsStore } from '@/stores/analytics'
import Money from '@/shared/ui/Money.vue'
import { formatDays, formatMoney, formatPercent } from '@/shared/utils/format'
import { drilldownToInventory, nav } from '@/app/navigation'
import { hapticImpact } from '@/shared/telegram/webapp'

const analytics = useAnalyticsStore()

// Автообновление: периодически, пока открыта вкладка, и при каждом возврате на неё.
const REFRESH_MS = 20000
let timer: number | undefined

onMounted(() => {
  if (!analytics.summary) void analytics.fetch()
  timer = window.setInterval(() => {
    // Свёрнутое приложение обновлять незачем: это разряд батареи и трафик
    // ради экрана, которого никто не видит.
    if (nav.activeTab === 'bi' && document.visibilityState === 'visible') {
      void analytics.fetch(true)
    }
  }, REFRESH_MS)
})

watch(
  () => nav.activeTab,
  (tab) => {
    if (tab === 'bi') void analytics.fetch()
  },
)

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})

const summary = computed(() => analytics.summary)

// Максимум для нормировки SVG-баров оборачиваемости.
const maxAvgDays = computed(() => {
  const t = summary.value?.turnover ?? []
  return t.reduce((m, p) => Math.max(m, p.avg_days), 0) || 1
})

function drillStale(): void {
  const ids = summary.value?.stale.item_ids ?? []
  if (!ids.length) return
  hapticImpact('light')
  drilldownToInventory({ ids })
}

function barWidth(avgDays: number): string {
  return `${Math.max(4, (avgDays / maxAvgDays.value) * 100)}%`
}
</script>

<template>
  <div class="bi">
    <header class="head">
      <h1 class="title">Аналитика</h1>
      <button
        class="icon-btn"
        :class="{ busy: analytics.loading }"
        :disabled="analytics.loading"
        aria-label="Обновить"
        @click="analytics.fetch(true)"
      >
        <svg viewBox="0 0 24 24" width="19" height="19" fill="none" stroke="currentColor"
             stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M20 11a8 8 0 1 0-.9 4.6M20 5v6h-6" />
        </svg>
      </button>
    </header>

    <div v-if="analytics.loading && !summary" class="state hint">Загрузка…</div>
    <div v-else-if="analytics.error" class="state">
      <p class="negative">{{ analytics.error }}</p>
      <button class="retry" @click="analytics.fetch(true)">Повторить</button>
    </div>

    <div v-else-if="summary" class="content no-scrollbar">
      <!-- Главная цифра: ради неё открывают экран -->
      <section class="hero">
        <p class="hero-label">Заработано всего</p>
        <p class="hero-value">
          <Money :value="summary.total_profit" :colored="true" strong />
        </p>
      </section>

      <!-- Две плитки: что в работе и что застряло -->
      <div class="tiles">
        <div class="tile">
          <span class="tile-label">В работе</span>
          <span class="tile-value">{{ summary.active_count }}</span>
        </div>
        <button
          class="tile tile-act"
          :class="{ empty: summary.stale.count === 0 }"
          :disabled="summary.stale.count === 0"
          @click="drillStale"
        >
          <span class="tile-label">Зависшие</span>
          <span class="tile-value stale-value">{{ summary.stale.count }}</span>
          <span class="tile-note">дольше {{ summary.stale.threshold_days }} дней</span>
          <svg v-if="summary.stale.count > 0" class="chev" viewBox="0 0 24 24" width="16" height="16"
               fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"
               stroke-linejoin="round" aria-hidden="true">
            <path d="m9 6 6 6-6 6" />
          </svg>
        </button>
      </div>

      <!-- Окупаемость по точкам -->
      <section class="card">
        <h2 class="card-title">Окупаемость по точкам закупки</h2>
        <div v-if="summary.by_location.length" class="table loc">
          <div class="tr th">
            <span>Точка</span>
            <span class="c-num">Вложено</span>
            <span class="c-num">Прибыль</span>
            <span class="c-num">ROI</span>
          </div>
          <div v-for="row in summary.by_location" :key="row.location" class="tr">
            <span class="c-loc">{{ row.location || '—' }}</span>
            <span class="c-num num">{{ formatMoney(row.invested) }}</span>
            <span class="c-num num" :class="row.profit >= 0 ? 'positive' : 'negative'">
              {{ formatMoney(row.profit) }}
            </span>
            <span class="c-num num-strong">{{ formatPercent(row.roi_percent) }}</span>
          </div>
        </div>
        <p v-else class="empty-note">Пока нет данных по точкам.</p>
      </section>

      <!-- Оборачиваемость -->
      <section class="card">
        <h2 class="card-title">Оборачиваемость</h2>
        <p class="card-sub">Средний срок от закупки до продажи</p>
        <div v-if="summary.turnover.length" class="bars">
          <div v-for="(p, i) in summary.turnover" :key="i" class="bar-row">
            <div class="bar-head">
              <span class="bar-label">{{ p.category }}</span>
              <span class="bar-value num">{{ formatDays(p.avg_days) }}</span>
            </div>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: barWidth(p.avg_days) }" />
            </div>
            <span class="bar-meta">{{ p.period }} · {{ p.sold_count }} шт.</span>
          </div>
        </div>
        <p v-else class="empty-note">Пока нет продаж для оборачиваемости.</p>
      </section>
    </div>
  </div>
</template>

<style scoped>
.bi {
  display: flex;
  flex-direction: column;
  height: 100%;
}
/* Шапка без разделителя: плоскости различаются фоном, линии не нужны. */
.head {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8.5px;
  padding: calc(var(--safe-top) + 10px) var(--pad) 6px;
}
.title {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: -0.01em;
}
.icon-btn {
  flex: none;
  width: 33px;
  height: 33px;
  border-radius: 50%;
  background: var(--ink-2);
  color: var(--fg-1);
  display: grid;
  place-items: center;
}
.icon-btn.busy {
  opacity: 0.5;
}
.state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10.5px;
  padding: 35px 17.5px;
  text-align: center;
}
.retry {
  color: var(--brand);
  font-weight: 700;
}
.content {
  flex: 1;
  overflow-y: auto;
  /* Поля те же, что в списке вещей: экраны стоят на одной вертикали. */
  padding: 4px var(--pad) calc(var(--nav-height) + var(--safe-bottom) + 12px);
  display: flex;
  flex-direction: column;
  gap: 10.5px;
}

/* --------------------------- Главная цифра --------------------------- */
.hero {
  padding: 5px 0 3.5px;
}
.hero-label {
  margin: 0 0 5px;
  font-size: 11.5px;
  color: var(--fg-1);
}
.hero-value {
  margin: 0;
  font-size: 35px;
  line-height: 36.5px;
  font-weight: 700;
  letter-spacing: -0.035em;
  font-variant-numeric: tabular-nums;
}

/* --------------------------- Плитки --------------------------- */
.tiles {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8.5px;
}
.tile {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 12px;
  border-radius: var(--r-card);
  background: var(--ink-1);
  text-align: left;
  color: var(--fg-0);
}
.tile-label {
  font-size: 11.5px;
  color: var(--fg-1);
}
.tile-value {
  margin-top: 3.5px;
  font-size: 24.5px;
  line-height: 28px;
  font-weight: 700;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}
.tile-note {
  margin-top: auto;
  padding-top: 5px;
  font-size: 11px;
  line-height: 14px;
  color: var(--fg-2);
}
/* Зависшие — янтарь этапа подготовки: тот же сигнал, что в списке вещей. */
.stale-value {
  color: var(--s-prep);
}
.tile.empty .stale-value {
  color: var(--fg-0);
}
.tile-act:active:not(:disabled) {
  background: var(--ink-2);
}
.chev {
  position: absolute;
  top: 14px;
  right: 12px;
  color: var(--fg-2);
}

/* --------------------------- Карточки-секции --------------------------- */
.card {
  padding: var(--pad);
  border-radius: var(--r-card);
  background: var(--ink-1);
}
.card-title {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: -0.01em;
}
.card-sub {
  margin: 3.5px 0 0;
  font-size: 11.5px;
  color: var(--fg-1);
}
.card-title + .table,
.card-title + .bars,
.card-sub + .bars,
.card-title + .empty-note,
.card-sub + .empty-note {
  margin-top: 12px;
}

/* --------------------------- Таблицы --------------------------- */
.table {
  display: flex;
  flex-direction: column;
}
.tr {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 58px 52px 78px;
  gap: 5px;
  align-items: center;
  padding: 8px 0;
  font-size: 12px;
  border-bottom: 1px solid var(--ink-2);
}
.table.loc .tr {
  grid-template-columns: minmax(0, 1fr) 76px 76px 46px;
}
.tr:last-child {
  border-bottom: none;
  padding-bottom: 0;
}
/* Шапка таблицы — подпись, а не «эйебрау»: обычный регистр, без разрядки. */
.tr.th {
  padding-top: 0;
  font-size: 11px;
  color: var(--fg-2);
  white-space: nowrap;
}
.c-n,
.c-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.c-loc {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.note {
  margin: 10.5px 0 0;
  font-size: 11px;
  line-height: 1.45;
  color: var(--fg-2);
}
.empty-note {
  margin: 0;
  font-size: 11.5px;
  color: var(--fg-1);
}

/* --------------------------- Полосы оборачиваемости --------------------------- */
.bars {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.bar-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 7px;
  margin-bottom: 6px;
}
.bar-label {
  min-width: 0;
  font-size: 12px;
  font-weight: 650;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bar-value {
  flex: none;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
/* Полоса тонкая: она сравнивает величины, а не изображает объём. */
.bar-track {
  height: 5px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: var(--r-pill);
  background: var(--s-listed);
}
.bar-meta {
  display: block;
  margin-top: 5px;
  font-size: 11px;
  color: var(--fg-2);
}
</style>
