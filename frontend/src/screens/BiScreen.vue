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
    if (nav.activeTab === 'bi' && !analytics.loading) void analytics.fetch()
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
      <button class="refresh" :disabled="analytics.loading" @click="analytics.fetch()">
        Обновить
      </button>
    </header>

    <div v-if="analytics.loading && !summary" class="state hint">Загрузка…</div>
    <div v-else-if="analytics.error" class="state">
      <p class="negative">{{ analytics.error }}</p>
      <button class="retry" @click="analytics.fetch()">Повторить</button>
    </div>

    <div v-else-if="summary" class="content no-scrollbar">
      <!-- KPI -->
      <div class="kpi-row">
        <div class="kpi">
          <span class="kpi-label">Прибыль</span>
          <Money :value="summary.total_profit" :colored="true" strong />
        </div>
        <div class="kpi">
          <span class="kpi-label">В обороте</span>
          <span class="num-strong kpi-big">{{ summary.active_count }}</span>
        </div>
      </div>

      <!-- Зависшие -->
      <button
        class="stale"
        :class="{ empty: summary.stale.count === 0 }"
        :disabled="summary.stale.count === 0"
        @click="drillStale"
      >
        <div class="stale-left">
          <div class="stale-title">Зависшие товары</div>
          <div class="stale-sub hint">На складе &gt; {{ summary.stale.threshold_days }} дней</div>
        </div>
        <div class="stale-right">
          <span class="num-strong stale-count">{{ summary.stale.count }}</span>
          <span class="hint">шт.</span>
          <svg v-if="summary.stale.count > 0" viewBox="0 0 24 24" width="18" height="18" class="chev">
            <path fill="currentColor" d="m9 6 6 6-6 6" />
          </svg>
        </div>
      </button>

      <!-- Эффективность каналов -->
      <section v-if="summary.by_channel.length" class="card">
        <h2 class="card-title">Каналы</h2>
        <div class="table">
          <div class="tr th">
            <span class="c-ch">Канал</span>
            <span class="c-n">Выложено</span>
            <span class="c-n">Продано</span>
            <span class="c-n">Прибыль</span>
          </div>
          <div v-for="ch in summary.by_channel" :key="ch.channel_id" class="tr" :class="{ off: !ch.enabled }">
            <span class="c-ch">
              <span class="ch-name">{{ ch.title || ch.chat_id }}</span>
              <span v-if="ch.sell_through !== null" class="hint small">
                конверсия {{ Math.round(ch.sell_through) }}%<template v-if="ch.avg_days !== null">
                  · {{ Math.round(ch.avg_days) }} дн.</template>
              </span>
            </span>
            <span class="c-n">{{ ch.posted }}</span>
            <span class="c-n">{{ ch.sold }}</span>
            <span class="c-n"><Money :value="ch.profit" signed /></span>
          </div>
        </div>
        <p class="hint small note">
          Вещь может висеть в нескольких каналах сразу, и определить, который
          привёл покупателя, по данным нельзя. Продажа засчитывается каждому —
          поэтому сумма по каналам может превышать общую прибыль. Это сравнение
          каналов между собой, а не разбиение выручки.
        </p>
      </section>

      <!-- Окупаемость по точкам -->
      <section class="card">
        <h2 class="card-title">Окупаемость по точкам закупки</h2>
        <div v-if="summary.by_location.length" class="table">
          <div class="tr th">
            <span class="c-loc">Точка</span>
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
        <p v-else class="empty-note hint">Пока нет данных по точкам.</p>
      </section>

      <!-- Оборачиваемость -->
      <section class="card">
        <h2 class="card-title">Оборачиваемость (ср. дни до продажи)</h2>
        <div v-if="summary.turnover.length" class="bars">
          <div v-for="(p, i) in summary.turnover" :key="i" class="bar-row">
            <div class="bar-head">
              <span class="bar-label">{{ p.category }}</span>
              <span class="bar-meta hint">{{ p.period }} · {{ p.sold_count }} шт.</span>
            </div>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: barWidth(p.avg_days) }" />
              <span class="bar-value num">{{ formatDays(p.avg_days) }}</span>
            </div>
          </div>
        </div>
        <p v-else class="empty-note hint">Пока нет продаж для оборачиваемости.</p>
      </section>

      <div class="bottom-pad" />
    </div>
  </div>
</template>

<style scoped>
.bi {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: calc(var(--safe-top) + 12px) 16px 10px;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}
.refresh {
  color: var(--tg-theme-link-color);
  font-weight: 600;
  font-size: 14px;
  padding: 8px;
}
.state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
}
.retry {
  color: var(--tg-theme-link-color);
  font-weight: 700;
}
.content {
  flex: 1;
  overflow-y: auto;
  padding: 14px 16px calc(var(--nav-height) + var(--safe-bottom));
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.kpi-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.kpi {
  background: var(--tg-theme-secondary-bg-color);
  border-radius: var(--radius);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kpi-label {
  font-size: 12px;
  color: var(--tg-theme-hint-color);
}
.kpi :deep(.num-strong),
.kpi-big {
  font-size: 22px;
}
.stale {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  text-align: left;
  padding: 14px;
  border-radius: var(--radius);
  background: color-mix(in srgb, var(--accent-negative) 12%, var(--tg-theme-secondary-bg-color));
}
.stale.empty {
  background: var(--tg-theme-secondary-bg-color);
  opacity: 0.8;
}
.stale-title {
  font-size: 15px;
  font-weight: 700;
}
.stale-sub {
  font-size: 12px;
  margin-top: 2px;
}
.stale-right {
  display: flex;
  align-items: center;
  gap: 6px;
}
.stale-count {
  font-size: 24px;
  color: var(--accent-negative);
}
.stale.empty .stale-count {
  color: var(--tg-theme-text-color);
}
.chev {
  color: var(--tg-theme-hint-color);
}
.card {
  background: var(--tg-theme-secondary-bg-color);
  border-radius: var(--radius);
  padding: 14px;
}
.card-title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 700;
}
.table {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.tr {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1fr 0.7fr;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--tg-theme-bg-color);
  font-size: 13px;
  align-items: center;
}
.tr.th {
  font-size: 11px;
  color: var(--tg-theme-hint-color);
  text-transform: uppercase;
}
.c-loc {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.c-num {
  text-align: right;
}
.bars {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.bar-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 5px;
}
.bar-label {
  font-size: 13px;
  font-weight: 600;
}
.bar-meta {
  font-size: 11px;
}
.bar-track {
  position: relative;
  height: 22px;
  background: var(--tg-theme-bg-color);
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
}
.bar-fill {
  height: 100%;
  border-radius: var(--radius-sm);
  background: var(--tg-theme-link-color);
  min-width: 4px;
}
.bar-value {
  position: absolute;
  right: 8px;
  font-size: 12px;
  font-weight: 600;
}
.empty-note {
  font-size: 13px;
  margin: 0;
}
.bottom-pad {
  height: 8px;
}
.c-ch {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.ch-name {
  font-weight: 700;
  word-break: break-all;
}
.c-n {
  width: 72px;
  text-align: right;
  flex: none;
}
.tr.off {
  opacity: 0.5;
}
.small {
  font-size: 11px;
}
.note {
  margin-top: 10px;
  line-height: 1.4;
}
</style>
