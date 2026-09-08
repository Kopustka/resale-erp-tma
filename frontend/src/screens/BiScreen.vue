<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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

const STAGE_LABEL: Record<string, string> = {
  BOUGHT: 'Куплен',
  PREPARING: 'Подготовка',
  PHOTOGRAPHED: 'Отснято',
  LISTED: 'Выставлен',
  SHIPPED: 'Отправлен',
}
const STAGE_VAR: Record<string, string> = {
  BOUGHT: 'bought',
  PREPARING: 'prep',
  PHOTOGRAPHED: 'photo',
  LISTED: 'listed',
  SHIPPED: 'ship',
}

/**
 * Этапы без «Отправлен»: он конечный, и «сколько вещь пролежала проданной»
 * ничего не говорит о работе — только о том, давно ли её продали.
 */
const stages = computed(() =>
  (summary.value?.stages ?? []).filter((s) => s.status !== 'SHIPPED'),
)
/** Нормировка полос: длина относительно самого долгого этапа. */
const stageMax = computed(() =>
  Math.max(1, ...stages.value.map((s) => s.avg_days)),
)
/** Самый долгий этап — его и называем узким местом. */
const bottleneck = computed(() => {
  const list = stages.value.filter((s) => s.passes > 0)
  if (!list.length) return null
  return list.reduce((a, b) => (b.avg_days > a.avg_days ? b : a))
})

/** Месяцы: нормируем столбцы по наибольшей прибыли. */
const monthMax = computed(() =>
  Math.max(1, ...(summary.value?.by_month ?? []).map((m) => Math.abs(m.profit))),
)
function monthLabel(m: string): string {
  const [y, mo] = m.split('-')
  const names = ['янв','фев','мар','апр','май','июн','июл','авг','сен','окт','ноя','дек']
  return `${names[Number(mo) - 1] ?? mo} ${y.slice(2)}`
}

/** Показываем тот разрез, где вообще есть что показать. */
const groupTab = ref<'category' | 'brand'>('category')
const groups = computed(() =>
  groupTab.value === 'brand' ? (summary.value?.by_brand ?? []) : (summary.value?.by_category ?? []),
)

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

      <!-- Прибыль по месяцам -->
      <section class="card">
        <h2 class="card-title">Прибыль по месяцам</h2>
        <div v-if="summary.by_month.length" class="months">
          <div v-for="m in summary.by_month" :key="m.month" class="mo">
            <span class="mo-val num">{{ formatMoney(m.profit) }}</span>
            <span
              class="mo-bar"
              :class="{ neg: m.profit < 0 }"
              :style="{ height: Math.max(4, (Math.abs(m.profit) / monthMax) * 74) + 'px' }"
            />
            <span class="mo-name">{{ monthLabel(m.month) }}</span>
            <span class="mo-cnt">{{ m.sold }} шт</span>
          </div>
        </div>
        <p v-else class="empty-note">
          Появится после первой продажи: считаем по месяцу отправки.
        </p>
      </section>

      <!-- Что приносит деньги -->
      <section class="card">
        <div class="card-head">
          <h2 class="card-title">Что приносит деньги</h2>
          <div class="seg">
            <button :class="{ on: groupTab === 'category' }" @click="groupTab = 'category'">
              Категории
            </button>
            <button :class="{ on: groupTab === 'brand' }" @click="groupTab = 'brand'">
              Бренды
            </button>
          </div>
        </div>
        <div v-if="groups.length" class="table grp">
          <div class="tr th">
            <span>{{ groupTab === 'brand' ? 'Бренд' : 'Категория' }}</span>
            <span class="c-num">Продано</span>
            <span class="c-num">Наценка</span>
            <span class="c-num">Прибыль</span>
          </div>
          <div v-for="g in groups" :key="g.name" class="tr">
            <span class="c-name">
              <span class="g-name">{{ g.name }}</span>
              <span class="g-meta">
                <template v-if="g.avg_days !== null">{{ formatDays(g.avg_days) }} до продажи</template>
                <template v-else-if="g.frozen > 0">заморожено {{ formatMoney(g.frozen) }}</template>
                <template v-else>—</template>
              </span>
            </span>
            <span class="c-num num">{{ g.sold }}/{{ g.total }}</span>
            <span class="c-num num">{{ g.markup === null ? '—' : formatPercent(g.markup) }}</span>
            <span class="c-num num-strong" :class="g.profit > 0 ? 'positive' : ''">
              {{ formatMoney(g.profit) }}
            </span>
          </div>
        </div>
        <p v-else class="empty-note">Заполните бренд и категорию у вещей.</p>
      </section>

      <!-- Где вещи застревают -->
      <section class="card">
        <h2 class="card-title">Где вещи застревают</h2>
        <p v-if="bottleneck" class="card-sub">
          Дольше всего вещь ждёт на этапе «{{ STAGE_LABEL[bottleneck.status] }}» —
          в среднем {{ formatDays(bottleneck.avg_days) }}
        </p>
        <div v-if="stages.length" class="stages">
          <div v-for="st in stages" :key="st.status" class="stage">
            <div class="stage-top">
              <span class="stage-name">{{ STAGE_LABEL[st.status] || st.status }}</span>
              <span class="stage-days num">{{ formatDays(st.avg_days) }}</span>
            </div>
            <div class="stage-track">
              <span
                class="stage-fill"
                :style="{
                  width: Math.max(3, (st.avg_days / stageMax) * 100) + '%',
                  background: `var(--s-${STAGE_VAR[st.status]})`,
                }"
              />
            </div>
            <div class="stage-foot">
              <span v-if="st.now_here">сейчас там {{ st.now_here }}</span>
              <span v-else>сейчас пусто</span>
              <span>дольше всего {{ formatDays(st.max_days) }}</span>
            </div>
          </div>
        </div>
        <p v-else class="empty-note">Данных о переходах пока нет.</p>
      </section>

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

/* --- Прибыль по месяцам ------------------------------------------------- */
.months {
  display: flex;
  align-items: flex-end;
  justify-content: flex-start;
  gap: 6px;
  overflow-x: auto;
  padding-top: 4px;
  scrollbar-width: none;
}
.months::-webkit-scrollbar {
  display: none;
}
/* Не растягиваем: при одном месяце столбец занимал бы всю ширину и читался
   как заливка, а не как график. Растём только до разумной ширины. */
.mo {
  flex: 0 1 46px;
  min-width: 44px;
  max-width: 72px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.mo-val {
  font-size: 10px;
  color: var(--fg-1);
  white-space: nowrap;
}
/* Столбец растёт снизу: так ряд читается как график, а не как набор плиток. */
.mo-bar {
  width: 100%;
  border-radius: 5px 5px 2px 2px;
  background: var(--s-ship);
}
.mo-bar.neg {
  background: var(--danger);
}
.mo-name {
  font-size: 10.5px;
  color: var(--fg-1);
}
.mo-cnt {
  font-size: 9.5px;
  color: var(--fg-2);
}

/* --- Что приносит деньги ------------------------------------------------- */
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}
.card-head .card-title {
  margin: 0;
}
.seg {
  display: flex;
  gap: 3px;
  padding: 2px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
}
.seg button {
  padding: 4px 9px;
  border-radius: var(--r-pill);
  color: var(--fg-1);
  font-size: 11px;
  font-weight: 650;
}
.seg button.on {
  background: var(--brand);
  color: var(--brand-ink);
}
.table.grp .tr {
  grid-template-columns: minmax(0, 1fr) 48px 52px 72px;
}
.c-name {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.g-name {
  font-weight: 650;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.g-meta {
  font-size: 10.5px;
  color: var(--fg-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* --- Где вещи застревают ------------------------------------------------- */
.card-sub {
  margin: 0 0 12px;
  font-size: 12.5px;
  line-height: 17px;
  color: var(--fg-1);
}
.stages {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.stage-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.stage-name {
  font-size: 13px;
  font-weight: 650;
}
.stage-days {
  font-size: 13px;
  font-weight: 700;
}
.stage-track {
  height: 5px;
  margin: 5px 0 4px;
  border-radius: 3px;
  background: var(--ink-2);
  overflow: hidden;
}
.stage-fill {
  display: block;
  height: 100%;
  border-radius: 3px;
}
.stage-foot {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 10.5px;
  color: var(--fg-2);
}
</style>
