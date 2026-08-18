<script setup lang="ts">
/**
 * Деталь товара: просмотр + редактирование полей, смена статуса/состояния,
 * архивация/восстановление и безвозвратное удаление.
 * Работает с живым объектом из стора (items.items) по id из nav.
 */
import { computed, reactive, ref, watch } from 'vue'
import AuthImage from '@/shared/ui/AuthImage.vue'
import DiscountSheet from '@/components/DiscountSheet.vue'
import { discountsApi } from '@/shared/api/endpoints'
import type { Discount } from '@/shared/api/types'
import StatusBadge from '@/shared/ui/StatusBadge.vue'
import { itemsApi } from '@/shared/api/endpoints'
import { useItemsStore } from '@/stores/items'
import { useSessionStore } from '@/stores/session'
import { useToastStore } from '@/stores/toast'
import { nav, closeOverlay } from '@/app/navigation'
import { hapticImpact, hapticNotify } from '@/shared/telegram/webapp'
import {
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

// --- Скидки ---
const discountOpen = ref(false)

/**
 * Почему скидку сделать нельзя. null — можно.
 * Объявление уходит ответом на пост вещи, поэтому до публикации
 * отвечать не на что.
 */
const discountBlock = computed<string | null>(() => {
  const it = item.value
  if (!it) return 'Вещь не загружена'
  if (it.status !== 'LISTED') return 'Скидку можно сделать только на выложенную вещь'
  if (it.list_price === null || it.list_price === undefined)
    return 'Сначала укажите цену продажи — от неё считается скидка'
  return null
})
const discounts = ref<Discount[]>([])

async function loadDiscounts(): Promise<void> {
  const it = item.value
  if (!it) return
  try {
    discounts.value = await discountsApi.list(it.id, true)
  } catch {
    /* история необязательна — молчим */
  }
}

async function onDiscountCreated(): Promise<void> {
  await loadDiscounts()
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

function discountWhen(d: Discount): string {
  if (!d.scheduled_at) return 'сразу'
  return new Date(d.scheduled_at).toLocaleString('ru-RU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

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
watch(() => nav.detailItemId, () => void loadDiscounts(), { immediate: true })

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

// AI-перегенерация: результат подставляется в форму, юзер правит и сохраняет.
const regenerating = ref(false)

async function regenerate(): Promise<void> {
  const it = item.value
  if (!it || regenerating.value) return
  if (it.photo_count === 0) {
    toast.error('У вещи нет фото — добавьте хотя бы одно')
    return
  }
  regenerating.value = true
  try {
    const gen = await itemsApi.aiDescribe(it.id)
    form.title = gen.title
    form.description = gen.description
    hapticNotify('success')
    toast.success('Сгенерировано — проверь и сохрани')
  } catch (e) {
    hapticNotify('error')
    toast.error(e instanceof Error ? e.message : 'Не удалось сгенерировать')
  } finally {
    regenerating.value = false
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
        <svg viewBox="0 0 24 24" width="24" height="24">
          <path fill="currentColor" d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" />
        </svg>
      </button>
      <div class="head-title">
        <span class="sku num">{{ item.sku }}</span>
        <StatusBadge :status="item.status" />
      </div>
    </header>

    <div class="scroll no-scrollbar">
      <!-- Фото -->
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
        <div class="status-now">
          Сейчас: <b>{{ STATUS_LABELS[item.status] }}</b>
        </div>
        <button
          v-if="rollbackTarget"
          class="rollback tap"
          :disabled="busy"
          @click="changeStatus(rollbackTarget)"
        >
          ↩ Откатить в «{{ STATUS_LABELS[rollbackTarget] }}»
        </button>
        <div v-if="transitions.length" class="chips">
          <button
            v-for="t in transitions"
            :key="t"
            class="chip tap"
            :disabled="busy"
            @click="changeStatus(t)"
          >
            {{ STATUS_LABELS[t] }}
          </button>
        </div>
        <p v-else class="hint small">Дальнейших переходов нет.</p>
      </section>

      <!-- Скидка -->
      <section class="block">
        <h2 class="block-title">Скидка</h2>
        <div class="status-now">
          Цена:
          <template v-if="item.price_before_discount">
            <s class="old-price">{{ item.price_before_discount }}</s>
          </template>
          <b>{{ item.list_price ?? '—' }}</b>
        </div>
        <button class="wide tap" :disabled="!!discountBlock" @click="discountOpen = true">
          Сделать скидку
        </button>
        <p v-if="discountBlock" class="hint small">{{ discountBlock }}</p>

        <div v-if="discounts.length" class="dlist">
          <div v-for="d in discounts" :key="d.id" class="drow" :class="d.status.toLowerCase()">
            <span class="dmain">
              −{{ d.percent }}% · {{ d.old_price }} → <b>{{ d.new_price }}</b>
            </span>
            <span class="hint small">{{ discountWhen(d) }}</span>
            <button v-if="d.status === 'SCHEDULED'" class="link small tap" @click="cancelDiscount(d)">
              Отменить
            </button>
          </div>
        </div>
      </section>

      <!-- Основное -->
      <section class="block">
        <h2 class="block-title">Основное</h2>
        <div class="lbl-row">
          <label class="lbl">Название</label>
          <button class="regen-btn" :disabled="!form.title.trim()" @click="copyTitle">
            📋 Копировать
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
        <div class="lbl-row">
          <label class="lbl">Описание</label>
          <span v-if="item && items.aiPending[item.id]" class="gen-hint">✨ генерируется…</span>
          <button
            v-else-if="canEdit"
            class="regen-btn"
            :disabled="regenerating"
            @click="regenerate"
          >
            {{ regenerating ? 'Генерация…' : '✨ Перегенерировать' }}
          </button>
        </div>
        <textarea v-model="form.description" class="field area" rows="4" :disabled="!canEdit" />
      </section>

      <!-- Замеры -->
      <section class="block">
        <h2 class="block-title">Замеры, см</h2>
        <div class="grid3">
          <div>
            <label class="lbl">Длина</label>
            <input v-model="form.length_cm" class="field num" inputmode="decimal" placeholder="—" :disabled="!canEdit" />
          </div>
          <div>
            <label class="lbl">Ширина</label>
            <input v-model="form.width_cm" class="field num" inputmode="decimal" placeholder="—" :disabled="!canEdit" />
          </div>
          <div>
            <label class="lbl">Рукав</label>
            <input v-model="form.sleeve_cm" class="field num" inputmode="decimal" placeholder="—" :disabled="!canEdit" />
          </div>
        </div>
        <button class="copy-btn tap" :disabled="!canCopy" @click="copyCard">
          📋 Скопировать описание + замеры
        </button>
      </section>

      <!-- Закупка -->
      <section v-if="canFinance" class="block">
        <h2 class="block-title">Закупка</h2>
        <label class="lbl">Валюта закупки</label>
        <select v-model="form.cost_currency" class="field">
          <option v-for="c in CURRENCIES" :key="c" :value="c">{{ c }}</option>
        </select>
        <div class="grid2">
          <div>
            <label class="lbl">Себестоимость</label>
            <input v-model="form.cost_price" class="field num" inputmode="decimal" />
          </div>
          <div>
            <label class="lbl">Реставрация</label>
            <input v-model="form.restore_cost" class="field num" inputmode="decimal" />
          </div>
          <div>
            <label class="lbl">Доставка</label>
            <input v-model="form.delivery_cost" class="field num" inputmode="decimal" />
          </div>
          <div>
            <label class="lbl">Комиссия</label>
            <input v-model="form.platform_fee" class="field num" inputmode="decimal" />
          </div>
        </div>
      </section>

      <!-- Цена продажи -->
      <section v-if="canFinance" class="block">
        <h2 class="block-title">Цена продажи</h2>
        <label class="lbl">Валюта цены</label>
        <select v-model="form.price_currency" class="field">
          <option v-for="c in CURRENCIES" :key="c" :value="c">{{ c }}</option>
        </select>
        <div class="grid2">
          <div>
            <label class="lbl">Цена (в объявлении)</label>
            <input v-model="form.list_price" class="field num" inputmode="decimal" />
          </div>
          <div>
            <label class="lbl">Фактическая продажа</label>
            <input v-model="form.selling_price" class="field num" inputmode="decimal" />
          </div>
        </div>
        <div v-if="item.net_profit != null" class="profit-row">
          Прибыль:
          <b :class="item.net_profit >= 0 ? 'positive' : 'negative'">
            {{ Math.round(item.net_profit) }} {{ baseSymbol() }}
          </b>
          <span v-if="item.roi_percent != null" class="hint"> · ROI {{ Math.round(item.roi_percent) }}%</span>
          <span class="hint"> (в {{ session.baseCurrency }})</span>
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

    <DiscountSheet v-model="discountOpen" :item="item" @created="onDiscountCreated" />
</template>

<style scoped>
.detail {
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
  gap: 10px;
  padding: calc(var(--safe-top) + 10px) 12px 10px;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.head-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.sku {
  font-size: 16px;
  font-weight: 700;
}
.scroll {
  flex: 1;
  overflow-y: auto;
  padding: 8px 16px;
}
.photos {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding: 8px 0;
}
.photo {
  flex: none;
  width: 120px;
  height: 120px;
}
.block {
  padding: 12px 0;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.block-title {
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--tg-theme-hint-color);
  margin: 0 0 10px;
}
.status-now {
  font-size: 14px;
  margin-bottom: 10px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.chip {
  padding: 8px 14px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-link-color);
  font-weight: 600;
  font-size: 14px;
}
.chip:disabled {
  opacity: 0.5;
}
.rollback {
  display: block;
  width: 100%;
  margin-bottom: 10px;
  padding: 10px 14px;
  border-radius: var(--radius);
  border: 1px dashed var(--tg-theme-hint-color);
  background: transparent;
  color: var(--tg-theme-text-color);
  font-weight: 600;
  font-size: 14px;
  text-align: left;
}
.rollback:disabled {
  opacity: 0.5;
}
.lbl-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}
.regen-btn {
  font-size: 13px;
  font-weight: 700;
  color: var(--tg-theme-link-color);
  padding: 6px 0;
}
.regen-btn:disabled {
  opacity: 0.5;
}
.copy-btn {
  width: 100%;
  min-height: var(--tap);
  margin-top: 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-link-color);
  font-weight: 700;
  font-size: 14px;
}
.copy-btn:disabled {
  opacity: 0.5;
}
.gen-hint {
  font-size: 13px;
  font-weight: 600;
  color: var(--tg-theme-link-color);
  animation: gen-pulse 1.4s ease-in-out infinite;
}
@keyframes gen-pulse {
  0%,
  100% {
    opacity: 0.45;
  }
  50% {
    opacity: 1;
  }
}
.small {
  font-size: 12px;
}
.lbl {
  display: block;
  font-size: 13px;
  color: var(--tg-theme-hint-color);
  margin: 10px 0 4px;
}
.field {
  width: 100%;
  min-height: var(--tap);
  padding: 10px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--tg-theme-secondary-bg-color);
  background: var(--tg-theme-secondary-bg-color);
  outline: none;
}
.field:disabled {
  opacity: 0.7;
}
.field:focus {
  border-color: var(--tg-theme-link-color);
}
.area {
  resize: vertical;
  min-height: 72px;
}
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.grid3 {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 10px;
}
.profit-row {
  margin-top: 12px;
  font-size: 15px;
}
.danger-block {
  border-bottom: none;
}
.actions {
  display: flex;
  gap: 10px;
}
.act {
  flex: 1;
  min-height: var(--tap);
  border-radius: var(--radius);
  font-weight: 700;
  font-size: 14px;
}
.act:disabled {
  opacity: 0.5;
}
.archive {
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
}
.restore {
  background: var(--accent-positive);
  color: #fff;
}
.delete {
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-destructive-text-color, #e53935);
}
.delete.armed {
  background: var(--tg-theme-destructive-text-color, #e53935);
  color: #fff;
}
.scroll-pad {
  height: 12px;
}
.sticky-save {
  padding: 10px 16px calc(var(--safe-bottom) + 10px);
  border-top: 1px solid var(--tg-theme-secondary-bg-color);
  background: var(--tg-theme-bg-color);
}
.save {
  width: 100%;
  min-height: var(--tap);
  border-radius: var(--radius);
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
  font-size: 16px;
  font-weight: 700;
}
.save:disabled {
  opacity: 0.5;
}
.old-price {
  color: var(--tg-theme-hint-color);
  margin-right: 6px;
}
.wide {
  width: 100%;
  min-height: var(--tap);
  margin-top: 10px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
  font-size: 15px;
  font-weight: 700;
}
.wide:disabled {
  opacity: 0.5;
}
.dlist {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.drow {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
}
.drow.published {
  opacity: 0.65;
}
.drow.cancelled {
  opacity: 0.45;
  text-decoration: line-through;
}
.dmain {
  flex: 1;
  font-size: 13px;
}
</style>
