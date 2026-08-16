<script setup lang="ts">
/**
 * Контент-план: посты без привязки к товару и расписание публикаций.
 *
 * Лента по дням, а не сетка календаря: на телефоне месячная сетка даёт
 * крошечные ячейки, в которые всё равно не помещается текст поста.
 */
import { computed, onMounted, ref } from 'vue'
import { closeOverlay } from '@/app/navigation'
import { discountsApi, postsApi } from '@/shared/api/endpoints'
import DiscountSheet from '@/components/DiscountSheet.vue'
import type { Discount } from '@/shared/api/types'
import { ApiError } from '@/shared/api/http'
import { useToastStore } from '@/stores/toast'
import { hapticImpact, hapticSelection } from '@/shared/telegram/webapp'
import type { CustomPost } from '@/shared/api/types'

const toast = useToastStore()

const list = ref<CustomPost[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const showDone = ref(false)

const composing = ref(false)
const body = ref('')
const when = ref('')
const saving = ref(false)
const confirmId = ref<string | null>(null)

// --- Скидки в плане ---
const discountOpen = ref(false)
const discounts = ref<Discount[]>([])

async function loadDiscounts(): Promise<void> {
  try {
    discounts.value = await discountsApi.list(undefined, showDone.value)
  } catch {
    /* необязательно — молчим */
  }
}

async function cancelDiscount(d: Discount): Promise<void> {
  try {
    await discountsApi.cancel(d.id)
    toast.success('Скидка отменена')
    await loadDiscounts()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось отменить')
  }
}

function discountTime(d: Discount): string {
  if (!d.scheduled_at) return 'сразу'
  return new Date(d.scheduled_at).toLocaleString('ru-RU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function msg(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.message || fallback
  return e instanceof Error ? e.message : fallback
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    list.value = await postsApi.list(showDone.value)
  } catch (e) {
    error.value = msg(e, 'Не удалось загрузить контент-план')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void load()
  void loadDiscounts()
})

/** Минимум для input[type=datetime-local] — сейчас, в местном времени. */
const minWhen = computed(() => {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
})

/** Группировка по дню: ключ — дата, значение — посты этого дня. */
const grouped = computed(() => {
  const out: { day: string; posts: CustomPost[] }[] = []
  const byDay = new Map<string, CustomPost[]>()
  for (const p of list.value) {
    const day = p.scheduled_at
      ? new Date(p.scheduled_at).toLocaleDateString('ru-RU', {
          day: 'numeric',
          month: 'long',
          weekday: 'short',
        })
      : 'Без даты — сразу'
    const arr = byDay.get(day)
    if (arr) arr.push(p)
    else byDay.set(day, [p])
  }
  for (const [day, posts] of byDay) out.push({ day, posts })
  return out
})

function timeOf(p: CustomPost): string {
  if (!p.scheduled_at) return '—'
  return new Date(p.scheduled_at).toLocaleTimeString('ru-RU', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

const STATUS_LABEL: Record<CustomPost['status'], string> = {
  SCHEDULED: 'Запланирован',
  PUBLISHED: 'Опубликован',
  CANCELLED: 'Отменён',
}

async function create(): Promise<void> {
  const text = body.value.trim()
  if (!text || saving.value) return
  saving.value = true
  try {
    // datetime-local отдаёт время без зоны — переводим в UTC явно,
    // иначе сервер прочитает местное как UTC и пост уйдёт не тогда.
    const iso = when.value ? new Date(when.value).toISOString() : null
    await postsApi.create(text, iso)
    hapticImpact('medium')
    toast.success(iso ? 'Пост запланирован' : 'Пост отправлен в канал')
    body.value = ''
    when.value = ''
    composing.value = false
    await load()
  } catch (e) {
    toast.error(msg(e, 'Не удалось создать пост'))
  } finally {
    saving.value = false
  }
}

async function cancel(p: CustomPost): Promise<void> {
  confirmId.value = null
  try {
    await postsApi.cancel(p.id)
    hapticSelection()
    toast.success('Пост отменён')
    await load()
  } catch (e) {
    toast.error(msg(e, 'Не удалось отменить'))
  }
}

async function toggleDone(): Promise<void> {
  showDone.value = !showDone.value
  await load()
}
</script>

<template>
  <div class="calendar">
    <header class="head">
      <button class="close tap" aria-label="Закрыть" @click="closeOverlay">
        <svg viewBox="0 0 24 24" width="24" height="24">
          <path fill="currentColor" d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" />
        </svg>
      </button>
      <h1 class="title">Контент-план</h1>
      <button class="toggle tap" @click="toggleDone">
        {{ showDone ? 'Только план' : 'Показать все' }}
      </button>
    </header>

    <div class="scroll no-scrollbar">
      <section class="block">
        <template v-if="composing">
          <label class="lbl">Текст поста</label>
          <textarea
            v-model="body"
            class="field area"
            rows="4"
            maxlength="4000"
            placeholder="Завтра большой ресток — заходите в 18:00"
          />
          <label class="lbl">Когда опубликовать</label>
          <input v-model="when" type="datetime-local" class="field" :min="minWhen" />
          <p class="note">Оставьте пустым — уйдёт в канал сразу.</p>
          <div class="row">
            <button class="btn-primary tap" :disabled="!body.trim() || saving" @click="create">
              {{ saving ? '…' : when ? 'Запланировать' : 'Опубликовать' }}
            </button>
            <button class="btn-secondary tap" @click="composing = false">Отмена</button>
          </div>
        </template>
        <template v-else>
          <button class="btn-primary tap" @click="composing = true">+ Новый пост</button>
          <button class="btn-secondary tap gap-top" @click="discountOpen = true">
            % Скидка на вещь
          </button>
        </template>
      </section>

      <section v-if="discounts.length" class="block">
        <h2 class="day-title">Скидки</h2>
        <div v-for="d in discounts" :key="d.id" class="card" :class="d.status.toLowerCase()">
          <div class="card-head">
            <span class="time">{{ discountTime(d) }}</span>
            <span class="status">−{{ d.percent }}%</span>
          </div>
          <p class="body">
            {{ d.item_sku }} · {{ d.item_title }} — {{ d.old_price }} → <b>{{ d.new_price }}</b>
          </p>
          <div v-if="d.status === 'SCHEDULED'" class="actions">
            <button class="link negative tap" @click="cancelDiscount(d)">Отменить</button>
          </div>
        </div>
      </section>

      <section class="block">
        <p v-if="loading" class="hint">Загрузка…</p>
        <p v-else-if="error" class="negative">{{ error }}</p>
        <div v-else-if="!list.length" class="empty">
          <p>Постов нет.</p>
          <p class="note">
            Сюда попадают анонсы, ресток и всё, что не привязано к конкретной вещи.
            Можно опубликовать сразу или отложить на нужное время.
          </p>
        </div>

        <div v-for="g in grouped" :key="g.day" class="day">
          <h2 class="day-title">{{ g.day }}</h2>
          <div v-for="p in g.posts" :key="p.id" class="card" :class="p.status.toLowerCase()">
            <div class="card-head">
              <span class="time">{{ timeOf(p) }}</span>
              <span class="status">{{ STATUS_LABEL[p.status] }}</span>
              <span v-if="p.photo_count" class="hint">📷 {{ p.photo_count }}</span>
            </div>
            <p class="body">{{ p.body }}</p>
            <div v-if="p.status === 'SCHEDULED'" class="actions">
              <template v-if="confirmId === p.id">
                <button class="link negative tap" @click="cancel(p)">Точно отменить</button>
                <button class="link tap" @click="confirmId = null">Нет</button>
              </template>
              <button v-else class="link negative tap" @click="confirmId = p.id">Отменить</button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <DiscountSheet v-model="discountOpen" :item="null" @created="loadDiscounts" />
  </div>
</template>

<style scoped>
.calendar {
  position: fixed;
  inset: 0;
  z-index: 120;
  background: var(--tg-theme-bg-color);
  display: flex;
  flex-direction: column;
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: calc(var(--safe-top) + 10px) 12px 10px;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.close {
  display: flex;
  align-items: center;
  justify-content: center;
}
.title {
  flex: 1;
  font-size: 18px;
  font-weight: 700;
  margin: 0;
}
.toggle {
  font-size: 13px;
  font-weight: 700;
  color: var(--tg-theme-link-color);
  min-height: var(--tap);
}
.scroll {
  flex: 1;
  overflow-y: auto;
  padding: 8px 16px calc(var(--safe-bottom) + 24px);
}
.block {
  padding: 12px 0;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.block:last-child {
  border-bottom: none;
}
.lbl {
  display: block;
  font-size: 12px;
  color: var(--tg-theme-hint-color);
  margin: 10px 0 4px;
}
.field {
  width: 100%;
  min-height: var(--tap);
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
  border: 1px solid transparent;
  font: inherit;
}
.area {
  resize: vertical;
}
.note {
  font-size: 12px;
  color: var(--tg-theme-hint-color);
  margin: 8px 0 0;
  line-height: 1.4;
}
.hint {
  color: var(--tg-theme-hint-color);
  font-size: 12px;
}
.negative {
  color: var(--accent-negative);
}
.row {
  display: flex;
  gap: var(--gap);
  margin-top: 12px;
}
.btn-primary,
.btn-secondary {
  flex: 1;
  min-height: var(--tap);
  border-radius: var(--radius);
  font-size: 15px;
  font-weight: 700;
}
.btn-primary {
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
}
.btn-primary:disabled {
  opacity: 0.6;
}
.btn-secondary {
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
}
.day {
  margin-top: 16px;
}
.day-title {
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--tg-theme-hint-color);
  margin: 0 0 8px;
}
.card {
  background: var(--tg-theme-secondary-bg-color);
  border-radius: var(--radius);
  padding: 12px;
  margin-bottom: var(--gap);
  border-left: 3px solid var(--tg-theme-link-color);
}
.card.published {
  border-left-color: var(--accent-positive);
  opacity: 0.7;
}
.card.cancelled {
  border-left-color: var(--tg-theme-hint-color);
  opacity: 0.5;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.time {
  font-size: 15px;
  font-weight: 700;
}
.status {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--tg-theme-hint-color);
}
.body {
  margin: 8px 0 0;
  font-size: 14px;
  line-height: 1.4;
  white-space: pre-wrap;
  word-break: break-word;
}
.actions {
  display: flex;
  gap: 16px;
  margin-top: 8px;
}
.link {
  min-height: var(--tap);
  font-size: 14px;
  font-weight: 700;
  color: var(--tg-theme-link-color);
}
.link.negative {
  color: var(--accent-negative);
}
.empty {
  padding: 8px 0;
}
.gap-top {
  margin-top: var(--gap);
}
</style>
