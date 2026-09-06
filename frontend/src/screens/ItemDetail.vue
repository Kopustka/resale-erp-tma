<script setup lang="ts">
/**
 * Деталь товара: просмотр + редактирование полей, смена статуса/состояния,
 * архивация/восстановление и безвозвратное удаление.
 * Работает с живым объектом из стора (items.items) по id из nav.
 */
import { computed, reactive, ref, watch } from 'vue'
import AuthImage from '@/shared/ui/AuthImage.vue'
import StatusBadge from '@/shared/ui/StatusBadge.vue'
import { useItemsStore } from '@/stores/items'
import { useSessionStore } from '@/stores/session'
import { useToastStore } from '@/stores/toast'
import { nav, closeOverlay } from '@/app/navigation'
import { hapticImpact, hapticNotify } from '@/shared/telegram/webapp'
import {
  ACTION_LABELS,
  allowedTransitions,
  prevStatus,
  requiresListPrice,
  requiresSellingPrice,
  STATUS_LABELS,
} from '@/shared/utils/status'
import { copyText } from '@/shared/utils/clipboard'
import { baseSymbol } from '@/shared/utils/format'
import { CURRENCIES, type Currency, type ItemStatus, type ItemUpdate } from '@/shared/api/types'

const items = useItemsStore()
const session = useSessionStore()
const toast = useToastStore()

const item = computed(() => items.items.find((i) => i.id === nav.detailItemId) ?? null)
const canEdit = computed(() => session.canEdit)
const canFinance = computed(() => session.canSeeFinance)
const isArchived = computed(() => items.viewArchived)

const form = reactive({
  title: '',
  brand: '',
  category: '',
  size: '',
  color: '',
  condition: '',
  length_cm: '',
  width_cm: '',
  sleeve_cm: '',
  description: '',
  cost_price: '',
  restore_cost: '',
  delivery_cost: '',
  platform_fee: '',
  selling_price: '',
  list_price: '',
  cost_currency: 'BYN' as Currency,
  price_currency: 'BYN' as Currency,
})

const saving = ref(false)
const busy = ref(false)
const confirmDelete = ref(false)

function numToStr(v: number | null | undefined): string {
  return v === null || v === undefined ? '' : String(v)
}

/** Заполняет форму из текущего товара. */
function syncForm(): void {
  const it = item.value
  if (!it) return
  form.title = it.title
  form.brand = it.brand
  form.category = it.category
  form.size = it.size ?? ''
  form.color = it.color ?? ''
  form.condition = it.condition ?? ''
  form.length_cm = numToStr(it.length_cm)
  form.width_cm = numToStr(it.width_cm)
  form.sleeve_cm = numToStr(it.sleeve_cm)
  form.description = it.description ?? ''
  form.cost_price = numToStr(it.cost_price)
  form.restore_cost = numToStr(it.restore_cost)
  form.delivery_cost = numToStr(it.delivery_cost)
  form.platform_fee = numToStr(it.platform_fee)
  form.selling_price = numToStr(it.selling_price)
  form.list_price = numToStr(it.list_price)
  form.cost_currency = it.cost_currency ?? session.baseCurrency
  form.price_currency = it.price_currency ?? session.baseCurrency
}

watch(() => nav.detailItemId, syncForm, { immediate: true })

// Фоновая AI-генерация могла доехать, пока карточка открыта: подтягиваем
// новые название/описание в форму, но НЕ затираем то, что юзер уже правил.
watch(
  () => [item.value?.title, item.value?.description] as const,
  ([newTitle, newDescr], old) => {
    const [oldTitle] = old ?? [undefined, undefined]
    if (newDescr && newDescr.trim() && !form.description.trim()) {
      form.description = newDescr
    }
    // Название обновляем, только если юзер его не менял (в форме всё ещё
    // старое значение-заглушка или пусто).
    if (newTitle && (!form.title.trim() || form.title === oldTitle)) {
      form.title = newTitle
    }
  },
)

function toNumber(value: string): number | undefined {
  const n = Number(value.replace(/\s/g, '').replace(',', '.'))
  return Number.isFinite(n) && value.trim() !== '' ? n : undefined
}

/** Статус для отката (шаг назад по happy path). */
const rollbackTarget = computed<ItemStatus | null>(() =>
  item.value ? prevStatus(item.value.status) : null,
)

/** Остальные переходы — без откатного (он показан отдельной кнопкой). */
const transitions = computed<ItemStatus[]>(() =>
  item.value
    ? allowedTransitions(item.value.status).filter((t) => t !== rollbackTarget.value)
    : [],
)

async function save(): Promise<void> {
  const it = item.value
  if (!it || saving.value) return
  saving.value = true
  const patch: ItemUpdate = {
    title: form.title.trim(),
    brand: form.brand.trim(),
    category: form.category.trim(),
    size: form.size.trim() || null,
    color: form.color.trim() || null,
    condition: form.condition.trim() || null,
    length_cm: toNumber(form.length_cm) ?? null,
    width_cm: toNumber(form.width_cm) ?? null,
    sleeve_cm: toNumber(form.sleeve_cm) ?? null,
    description: form.description.trim() || null,
  }
  if (canFinance.value) {
    patch.cost_price = toNumber(form.cost_price) ?? 0
    patch.restore_cost = toNumber(form.restore_cost) ?? 0
    patch.delivery_cost = toNumber(form.delivery_cost) ?? 0
    patch.platform_fee = toNumber(form.platform_fee) ?? 0
    const sp = toNumber(form.selling_price)
    patch.selling_price = sp === undefined ? null : sp
    const lp = toNumber(form.list_price)
    patch.list_price = lp === undefined ? null : lp
    patch.cost_currency = form.cost_currency
    patch.price_currency = form.price_currency
  }
  try {
    await items.updateItem(it.id, patch)
    hapticNotify('success')
    toast.success('Сохранено')
    closeOverlay()
  } catch (e) {
    hapticNotify('error')
    toast.error(e instanceof Error ? e.message : 'Не удалось сохранить')
  } finally {
    saving.value = false
  }
}

async function changeStatus(target: ItemStatus): Promise<void> {
  const it = item.value
  if (!it || busy.value) return
  // Выставление требует цену в объявлении: без неё пост уйдёт без суммы.
  if (requiresListPrice(target)) {
    const listed = toNumber(form.list_price) ?? it.list_price
    if (listed === null || listed === undefined) {
      toast.error('Укажите цену продажи в блоке «Цена продажи» — без неё нельзя выставить')
      return
    }
  }
  // Отправка = продажа, поэтому требует цену: из формы или уже сохранённую.
  let sellingPrice: number | undefined
  if (requiresSellingPrice(target)) {
    sellingPrice = toNumber(form.selling_price)
    if (sellingPrice === undefined && (it.selling_price === null || it.selling_price === undefined)) {
      toast.error('Впишите цену продажи в блоке «Финансы»')
      return
    }
  }
  busy.value = true
  hapticImpact('light')
  const ok = await items.applyStatus(it, { targetStatus: target, sellingPrice })
  busy.value = false
  if (ok) {
    hapticNotify('success')
    syncForm()
  }
}

// Копирование карточки для Куфара: описание + замеры одним буфером.
function buildCardText(): string {
  const lines: string[] = []
  const descr = form.description.trim()
  if (descr) lines.push(descr)
  if (form.length_cm.trim()) lines.push(`Длина ${form.length_cm.trim()}`)
  if (form.width_cm.trim()) lines.push(`Ширина ${form.width_cm.trim()}`)
  if (form.sleeve_cm.trim()) lines.push(`Рукав ${form.sleeve_cm.trim()}`)
  return lines.join('\n')
}

const canCopy = computed(
  () =>
    !!form.description.trim() ||
    !!form.length_cm.trim() ||
    !!form.width_cm.trim() ||
    !!form.sleeve_cm.trim(),
)

async function copyCard(): Promise<void> {
  if (await copyText(buildCardText())) {
    hapticNotify('success')
    toast.success('Скопировано: описание + замеры')
  } else {
    toast.error('Не удалось скопировать')
  }
}

async function copyTitle(): Promise<void> {
  if (await copyText(form.title.trim())) {
    hapticNotify('success')
    toast.success('Название скопировано')
  } else {
    toast.error('Не удалось скопировать')
  }
}

async function doArchive(): Promise<void> {
  const it = item.value
  if (!it) return
  busy.value = true
  try {
    await items.archiveNow(it.id)
    hapticNotify('success')
    toast.success('В архиве')
    closeOverlay()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось')
  } finally {
    busy.value = false
  }
}

async function doRestore(): Promise<void> {
  const it = item.value
  if (!it) return
  busy.value = true
  try {
    await items.restore(it.id)
    hapticNotify('success')
    toast.success('Восстановлено')
    closeOverlay()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось')
  } finally {
    busy.value = false
  }
}

async function doDelete(): Promise<void> {
  const it = item.value
  if (!it) return
  if (!confirmDelete.value) {
    confirmDelete.value = true
    window.setTimeout(() => (confirmDelete.value = false), 3500)
    return
  }
  busy.value = true
  try {
    await items.hardDelete(it.id)
    hapticNotify('success')
    toast.success('Удалено навсегда')
    closeOverlay()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось удалить')
  } finally {
    busy.value = false
    confirmDelete.value = false
  }
}

const photoIndexes = computed(() =>
  item.value ? Array.from({ length: item.value.photo_count }, (_, i) => i) : [],
)
</script>

<template>
  <div v-if="item" class="detail">
    <header class="head">
      <button class="close tap" aria-label="Закрыть" @click="closeOverlay">
        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
          <path fill="none" d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
        </svg>
      </button>
      <div class="head-title">
        <span class="sku num">{{ item.sku }}</span>
        <StatusBadge :status="item.status" />
      </div>
    </header>

    <div class="scroll no-scrollbar">
      <!-- Фото: главный актив вещи, поэтому крупная лента во всю ширину -->
      <div v-if="photoIndexes.length" class="photos no-scrollbar">
        <div v-for="i in photoIndexes" :key="i" class="photo">
          <AuthImage
            :item-id="item.id"
            :index="i"
            :photo-count="item.photo_count"
            :alt="item.title"
            :width="1200"
          />
        </div>
      </div>

      <!-- Смена статуса -->
      <section v-if="canEdit && !isArchived" class="block">
        <h2 class="block-title">Статус</h2>
        <div class="card">
          <div class="status-now">
            <span class="cap">Сейчас</span>
            <b class="status-name">{{ STATUS_LABELS[item.status] }}</b>
          </div>
          <div v-if="transitions.length" class="chips">
            <button
              v-for="t in transitions"
              :key="t"
              class="chip tap"
              :disabled="busy"
              @click="changeStatus(t)"
            >
              <span>{{ ACTION_LABELS[t] }}</span>
              <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
                <path fill="none" d="M5 12h13m-5-6 6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
          </div>
          <p v-else class="empty-note">Дальнейших переходов нет.</p>
          <button
            v-if="rollbackTarget"
            class="rollback tap"
            :disabled="busy"
            @click="changeStatus(rollbackTarget)"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
              <path fill="none" d="M19 12H6m5-6-6 6 6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <span>Откатить в «{{ STATUS_LABELS[rollbackTarget] }}»</span>
          </button>
        </div>
      </section>

      <!-- Основное -->
      <section class="block">
        <h2 class="block-title">Основное</h2>
        <div class="lbl-row">
          <label class="lbl">Название</label>
          <button class="regen-btn" :disabled="!form.title.trim()" @click="copyTitle">
            Копировать
          </button>
        </div>
        <input v-model="form.title" class="field" :disabled="!canEdit" />
        <label class="lbl">Бренд</label>
        <input v-model="form.brand" class="field" :disabled="!canEdit" />
        <label class="lbl">Категория</label>
        <input v-model="form.category" class="field" :disabled="!canEdit" />
        <div class="grid2">
          <div>
            <label class="lbl">Размер</label>
            <input v-model="form.size" class="field" :disabled="!canEdit" />
          </div>
          <div>
            <label class="lbl">Цвет</label>
            <input v-model="form.color" class="field" :disabled="!canEdit" />
          </div>
        </div>
        <label class="lbl">Состояние</label>
        <input v-model="form.condition" class="field" placeholder="8/10" :disabled="!canEdit" />
        <label class="lbl">Описание</label>
        <textarea v-model="form.description" class="field area" rows="4" :disabled="!canEdit" />
      </section>

      <!-- Замеры -->
      <section class="block">
        <h2 class="block-title">Замеры, см</h2>
        <div class="grid3">
          <div>
            <label class="lbl">Длина</label>
            <input v-model="form.length_cm" class="field num big" inputmode="decimal" placeholder="—" :disabled="!canEdit" />
          </div>
          <div>
            <label class="lbl">Ширина</label>
            <input v-model="form.width_cm" class="field num big" inputmode="decimal" placeholder="—" :disabled="!canEdit" />
          </div>
          <div>
            <label class="lbl">Рукав</label>
            <input v-model="form.sleeve_cm" class="field num big" inputmode="decimal" placeholder="—" :disabled="!canEdit" />
          </div>
        </div>
        <button class="copy-btn tap" :disabled="!canCopy" @click="copyCard">
          Скопировать описание и замеры
        </button>
      </section>

      <!-- Закупка -->
      <section v-if="canFinance" class="block">
        <h2 class="block-title">Закупка</h2>
        <label class="lbl">Валюта закупки</label>
        <select v-model="form.cost_currency" class="field select">
          <option v-for="c in CURRENCIES" :key="c" :value="c">{{ c }}</option>
        </select>
        <div class="grid2">
          <div>
            <label class="lbl">Себестоимость</label>
            <input v-model="form.cost_price" class="field num big" inputmode="decimal" />
          </div>
          <div>
            <label class="lbl">Реставрация</label>
            <input v-model="form.restore_cost" class="field num big" inputmode="decimal" />
          </div>
          <div>
            <label class="lbl">Доставка</label>
            <input v-model="form.delivery_cost" class="field num big" inputmode="decimal" />
          </div>
          <div>
            <label class="lbl">Комиссия</label>
            <input v-model="form.platform_fee" class="field num big" inputmode="decimal" />
          </div>
        </div>
      </section>

      <!-- Цена продажи -->
      <section v-if="canFinance" class="block">
        <h2 class="block-title">Цена продажи</h2>
        <label class="lbl">Валюта цены</label>
        <select v-model="form.price_currency" class="field select">
          <option v-for="c in CURRENCIES" :key="c" :value="c">{{ c }}</option>
        </select>
        <div class="grid2">
          <div>
            <label class="lbl">Цена в объявлении</label>
            <input v-model="form.list_price" class="field num big" inputmode="decimal" />
          </div>
          <div>
            <label class="lbl">Фактическая продажа</label>
            <input v-model="form.selling_price" class="field num big" inputmode="decimal" />
          </div>
        </div>
        <div v-if="item.net_profit != null" class="profit-row">
          <span class="cap">Прибыль</span>
          <b class="profit-value num" :class="item.net_profit >= 0 ? 'positive' : 'negative'">
            {{ Math.round(item.net_profit) }} {{ baseSymbol() }}
          </b>
          <span v-if="item.roi_percent != null" class="profit-note num">ROI {{ Math.round(item.roi_percent) }}%</span>
          <span class="profit-note">в {{ session.baseCurrency }}</span>
        </div>
      </section>

      <!-- Действия -->
      <section v-if="canEdit" class="block danger-block">
        <div class="actions">
          <button v-if="isArchived" class="act restore" :disabled="busy" @click="doRestore">
            Достать из архива
          </button>
          <button v-else class="act archive" :disabled="busy" @click="doArchive">
            В архив
          </button>
          <button class="act delete" :class="{ armed: confirmDelete }" :disabled="busy" @click="doDelete">
            {{ confirmDelete ? 'Точно удалить?' : 'Удалить навсегда' }}
          </button>
        </div>
      </section>

      <div class="scroll-pad" />
    </div>

    <div v-if="canEdit" class="sticky-save">
      <button class="save tap" :disabled="saving" @click="save">
        {{ saving ? 'Сохранение…' : 'Сохранить изменения' }}
      </button>
    </div>
  </div>

</template>

<style scoped>
/*
 * Дизайн-система: тёмный холст --ink-0, на нём «плавают» поля и карточки
 * --ink-1 со скруглением --r-field/--r-card. Разделители-линии убраны —
 * группировка держится на заголовках 17px и воздухе. Ни теней, ни градиентов.
 */
.detail {
  position: fixed;
  inset: 0;
  z-index: 120;
  background: var(--ink-0);
  color: var(--fg-0);
  display: flex;
  flex-direction: column;
}

/* ------------------------------- Шапка ------------------------------- */
.head {
  display: flex;
  align-items: center;
  gap: 8.5px;
  padding: calc(var(--safe-top) + 10px) var(--pad) 10px;
  background: var(--ink-0);
}
.close {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: none;
  width: 35px;
  height: 35px;
  min-width: 35px;
  min-height: 35px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
  color: var(--fg-0);
}
.close:active {
  background: var(--ink-3);
}
.head-title {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
/* Артикул — служебное число: мелко, приглушённо, но табличными цифрами. */
.sku {
  padding: 4.5px 8.5px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
  color: var(--fg-2);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.scroll {
  flex: 1;
  overflow-y: auto;
  padding: 4px var(--pad) 0;
}

/* ------------------------------- Фото ------------------------------- */
/* Вещь продаёт фотография: даём ей высоту почти в треть экрана. */
.photos {
  display: flex;
  gap: var(--gap);
  overflow-x: auto;
  padding: 7px 0 3.5px;
  scroll-snap-type: x mandatory;
}
.photo {
  flex: none;
  width: 132px;
  height: 170.5px;
  scroll-snap-align: start;
}
.photo :deep(.auth-image) {
  border-radius: 12px;
  background: var(--ink-1);
}

/* ------------------------------ Секции ------------------------------ */
.block {
  padding: 14px 0 3.5px;
}
.block-title {
  font-size: 15px;
  font-weight: 650;
  letter-spacing: -0.015em;
  color: var(--fg-0);
  margin: 0 0 8.5px;
}
.card {
  background: var(--ink-1);
  border-radius: var(--r-card);
  padding: 12px;
}

/* --------------------------- Статус и шаги --------------------------- */
.status-now {
  display: flex;
  align-items: baseline;
  gap: 7px;
  margin-bottom: 10.5px;
}
.cap {
  font-size: 11px;
  color: var(--fg-2);
}
.status-name {
  font-size: 17.5px;
  font-weight: 700;
  letter-spacing: -0.025em;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--gap);
}
/* Главное действие экрана — перевести вещь на следующий этап. */
.chip {
  flex: 1 1 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  height: 43.5px;
  padding: 0 17.5px;
  border-radius: var(--r-pill);
  background: var(--brand);
  color: var(--brand-ink);
  font-weight: 700;
  font-size: 13px;
  letter-spacing: -0.01em;
}
.chip:active {
  background: color-mix(in srgb, var(--brand) 82%, #000);
}
.chip:disabled {
  opacity: 0.45;
}
/* Откат — вспомогательный жест, поэтому мелкая пилюля-призрак. */
.rollback {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  /* Перебиваем утилиту .tap (min-height 44px): откат — мелкая пилюля. */
  min-height: 26px;
  margin-top: 10.5px;
  padding: 0 12px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
  color: var(--fg-1);
  font-weight: 600;
  font-size: 11px;
}
.rollback:active {
  background: var(--ink-3);
}
.rollback:disabled {
  opacity: 0.45;
}
.empty-note {
  margin: 0;
  font-size: 11.5px;
  color: var(--fg-2);
}

/* --------------------------- Поля и подписи --------------------------- */
.lbl-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 7px;
}
.lbl {
  display: block;
  font-size: 11px;
  color: var(--fg-2);
  margin: 10.5px 0 5px;
}
.lbl-row .lbl {
  margin-bottom: 5px;
}
.regen-btn {
  flex: none;
  height: 26px;
  padding: 0 12px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
  color: var(--fg-1);
  font-size: 11px;
  font-weight: 600;
}
.regen-btn:active {
  background: var(--ink-3);
}
.regen-btn:disabled {
  opacity: 0.45;
}
.field {
  width: 100%;
  height: 43.5px;
  padding: 0 12px;
  border: none;
  border-radius: var(--r-field);
  background-color: var(--ink-1);
  color: var(--fg-0);
  font-size: 14px;
  outline: none;
  appearance: none;
  -webkit-appearance: none;
}
.field::placeholder {
  color: var(--fg-2);
}
.field:disabled {
  opacity: 0.6;
}
.field:focus {
  background-color: var(--ink-2);
}
/* Свой шеврон вместо системного: appearance: none его убирает. */
.select {
  padding-right: 35px;
  background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23767D89' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 14px center;
  background-size: 18px 18px;
}
/* Числа в полях — тот же вес, что и суммы в списке вещей. */
.big {
  font-size: 17.5px;
  font-weight: 700;
  letter-spacing: -0.025em;
}
.area {
  height: auto;
  min-height: 94px;
  padding: 12px;
  line-height: 1.45;
  resize: vertical;
}
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 var(--gap);
}
.grid3 {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 0 var(--gap);
}
.copy-btn {
  width: 100%;
  height: 43.5px;
  margin-top: 14px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
  color: var(--fg-0);
  font-weight: 700;
  font-size: 13px;
}
.copy-btn:active {
  background: var(--ink-3);
}
.copy-btn:disabled {
  opacity: 0.45;
}

/* ------------------------------ Прибыль ------------------------------ */
.profit-row {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 3.5px 8.5px;
  margin-top: 14px;
  padding: 12px;
  background: var(--ink-1);
  border-radius: var(--r-card);
}
.profit-value {
  font-size: 21px;
  font-weight: 700;
  letter-spacing: -0.025em;
}
.profit-note {
  font-size: 11px;
  color: var(--fg-2);
}

/* ------------------------------ Действия ------------------------------ */
.danger-block {
  padding-top: 17.5px;
}
.actions {
  display: flex;
  gap: var(--gap);
}
.act {
  flex: 1;
  height: 43.5px;
  padding: 0 10.5px;
  border-radius: var(--r-pill);
  font-weight: 700;
  font-size: 13px;
  letter-spacing: -0.01em;
}
.act:disabled {
  opacity: 0.45;
}
.archive {
  background: var(--ink-2);
  color: var(--fg-0);
}
.archive:active {
  background: var(--ink-3);
}
.restore {
  background: color-mix(in srgb, var(--s-ship) 15%, transparent);
  color: var(--s-ship);
}
.delete {
  background: color-mix(in srgb, var(--danger) 15%, transparent);
  color: var(--danger);
}
.delete.armed {
  background: var(--danger);
  color: var(--brand-ink);
}
.scroll-pad {
  height: 17.5px;
}

/* --------------------------- Липкое сохранение --------------------------- */
.sticky-save {
  padding: 10px var(--pad) calc(var(--safe-bottom) + 12px);
  background: var(--ink-0);
}
.save {
  width: 100%;
  height: 43.5px;
  border-radius: var(--r-pill);
  background: var(--brand);
  color: var(--brand-ink);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.01em;
}
.save:active {
  background: color-mix(in srgb, var(--brand) 82%, #000);
}
.save:disabled {
  opacity: 0.45;
}
</style>
