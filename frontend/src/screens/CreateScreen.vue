<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import AutocompleteInput from '@/components/AutocompleteInput.vue'
import { useItemsStore } from '@/stores/items'
import { useSessionStore } from '@/stores/session'
import { useToastStore } from '@/stores/toast'
import { closeOverlay, setTab } from '@/app/navigation'
import { hapticNotify } from '@/shared/telegram/webapp'
import type { ItemCreate } from '@/shared/api/types'
import { mediaApi } from '@/shared/api/endpoints'
import { downscaleForUpload } from '@/shared/utils/image'
import { CURRENCIES, type Currency } from '@/shared/api/types'

const items = useItemsStore()
const session = useSessionStore()
const toast = useToastStore()

const form = reactive({
  title: '',
  brand: '',
  category: '',
  size: '',
  color: '',
  condition: '',
  description: '',
  purchase_location: '',
  sales_platform: '',
  ad_url: '',
  cost_price: '',
  restore_cost: '',
  delivery_cost: '',
  list_price: '',
  cost_currency: session.baseCurrency as Currency,
  price_currency: session.baseCurrency as Currency,
})

interface PhotoEntry {
  id: string | null // "local:<name>" после успешной загрузки
  preview: string // objectURL для мгновенного превью
  uploading: boolean
  error: boolean
}

const photos = ref<PhotoEntry[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const submitting = ref(false)

const anyUploading = computed(() => photos.value.some((p) => p.uploading))

// Название не обязательно: при наличии фото его сгенерирует AI при сохранении.
const canSubmit = computed(
  () =>
    form.brand.trim().length > 0 &&
    form.category.trim().length > 0 &&
    !submitting.value &&
    !anyUploading.value,
)

function pickPhotos(): void {
  fileInput.value?.click()
}

async function onFilesSelected(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = '' // сброс, чтобы можно было выбрать тот же файл повторно
  if (!files.length) return

  // Превью показываем сразу, до отправки: пользователь видит, что фото
  // приняты, и не жмёт кнопку повторно.
  const entries = files.map((file) => {
    photos.value.push({
      id: null,
      preview: URL.createObjectURL(file),
      uploading: true,
      error: false,
    })
    // берём реактивный прокси из массива (мутировать исходный объект нельзя)
    return { file, entry: photos.value[photos.value.length - 1] }
  })

  let saved = 0
  let sentBytes = 0
  let originalBytes = 0

  // Параллельно: раньше снимки уходили строго по очереди, и четыре фото
  // складывались в четыре последовательных выгрузки. Ограничение в три
  // потока — чтобы не забить мобильный канал целиком.
  const queue = [...entries]
  async function worker(): Promise<void> {
    for (;;) {
      const next = queue.shift()
      if (!next) return
      const { file, entry } = next
      try {
        // Уменьшаем в браузере: телефон отдаёт 2–4 МБ на кадр, и это
        // главная причина долгого добавления, а не работа сервера.
        const small = await downscaleForUpload(file)
        originalBytes += small.originalBytes
        sentBytes += small.bytes
        const { photo_id } = await mediaApi.upload(small.file)
        entry.id = photo_id
        saved += 1
      } catch (err) {
        entry.error = true
        toast.error(err instanceof Error ? err.message : 'Не удалось загрузить фото')
      } finally {
        entry.uploading = false
      }
    }
  }
  await Promise.all([worker(), worker(), worker()])

  if (saved && originalBytes > sentBytes * 1.5) {
    const mb = (n: number) => (n / 1024 / 1024).toFixed(1)
    toast.show({
      message: `Фото сжаты: ${mb(originalBytes)} → ${mb(sentBytes)} МБ`,
      kind: 'info',
    })
  }
}

function removePhoto(idx: number): void {
  const [removed] = photos.value.splice(idx, 1)
  if (removed) URL.revokeObjectURL(removed.preview)
}

onBeforeUnmount(() => {
  photos.value.forEach((p) => URL.revokeObjectURL(p.preview))
})

function toNumber(value: string): number | undefined {
  const n = Number(value.replace(/\s/g, '').replace(',', '.'))
  return Number.isFinite(n) && value.trim() !== '' ? n : undefined
}

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  submitting.value = true

  const payload: ItemCreate = {
    title: form.title.trim(),
    brand: form.brand.trim(),
    category: form.category.trim(),
  }
  if (form.size.trim()) payload.size = form.size.trim()
  if (form.color.trim()) payload.color = form.color.trim()
  if (form.condition.trim()) payload.condition = form.condition.trim()
  if (form.description.trim()) payload.description = form.description.trim()
  if (form.purchase_location.trim()) payload.purchase_location = form.purchase_location.trim()
  if (form.sales_platform.trim()) payload.sales_platform = form.sales_platform.trim()
  if (form.ad_url.trim()) payload.ad_url = form.ad_url.trim()
  const photoIds = photos.value
    .filter((p) => p.id !== null)
    .map((p) => p.id as string)
  if (photoIds.length) payload.photo_file_ids = photoIds

  if (session.canSeeFinance) {
    const cost = toNumber(form.cost_price)
    const restore = toNumber(form.restore_cost)
    const delivery = toNumber(form.delivery_cost)
    const list = toNumber(form.list_price)
    if (cost !== undefined) payload.cost_price = cost
    if (restore !== undefined) payload.restore_cost = restore
    if (delivery !== undefined) payload.delivery_cost = delivery
    if (list !== undefined) payload.list_price = list
    payload.cost_currency = form.cost_currency
    payload.price_currency = form.price_currency
  }

  try {
    const needTitle = !form.title.trim()
    const needDescr = !form.description.trim()
    const created = await items.create(payload)
    hapticNotify('success')
    // Фоновая AI-генерация: если есть фото и название/описание пустые —
    // сервер дозаполнит, трекер покажет индикатор в списке и подтянет результат.
    const aiPending = (payload.photo_file_ids?.length ?? 0) > 0 && (needTitle || needDescr)
    toast.success(aiPending ? `Добавлен ${created.sku} · ✨ генерирую…` : `Добавлен ${created.sku}`)
    if (aiPending) void items.trackAiGeneration(created.id, created.title, needTitle, needDescr)
    closeOverlay()
    setTab('inventory')
  } catch (e) {
    hapticNotify('error')
    toast.error(e instanceof Error ? e.message : 'Не удалось сохранить')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="create">
    <header class="head">
      <button class="close tap" aria-label="Закрыть" @click="closeOverlay">
        <svg viewBox="0 0 24 24" width="24" height="24">
          <path fill="currentColor" d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" />
        </svg>
      </button>
      <h1 class="title">Новый товар</h1>
    </header>

    <div class="scroll no-scrollbar">

      <!-- Фото -->
      <section class="block">
        <h2 class="block-title">Фото</h2>
        <div class="photo-grid">
          <div v-for="(p, i) in photos" :key="i" class="thumb" :class="{ err: p.error }">
            <img :src="p.preview" alt="" class="thumb-img" />
            <div v-if="p.uploading" class="thumb-overlay"><span class="spinner" /></div>
            <div v-else-if="p.error" class="thumb-overlay err-mark">!</div>
            <button class="thumb-x" aria-label="Удалить фото" @click="removePhoto(i)">×</button>
          </div>
          <button class="add-tile tap" type="button" @click="pickPhotos">
            <svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
              <path fill="currentColor" d="M9 3 7.2 5H4a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-3.2L15 3H9zm3 5a5 5 0 1 1 0 10 5 5 0 0 1 0-10zm0 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6z" />
            </svg>
            <span class="add-label">Добавить</span>
          </button>
        </div>
        <p class="note">Из галереи или камеры телефона. Можно несколько.</p>
        <input
          ref="fileInput"
          type="file"
          accept="image/*"
          multiple
          class="hidden-input"
          @change="onFilesSelected"
        />
      </section>

      <!-- Основное -->
      <section class="block">
        <h2 class="block-title">Основное</h2>
        <label class="lbl">Название</label>
        <input
          v-model="form.title"
          class="field"
          :placeholder="photos.length ? 'Пусто — сгенерируется по фото ✨' : 'Напр. Куртка кожаная'"
        />

        <label class="lbl">Бренд *</label>
        <AutocompleteInput v-model="form.brand" field="brand" placeholder="Напр. Prada" />

        <label class="lbl">Категория *</label>
        <AutocompleteInput v-model="form.category" field="category" placeholder="Напр. Верхняя одежда" />

        <div class="grid2">
          <div>
            <label class="lbl">Размер</label>
            <input v-model="form.size" class="field" placeholder="M / 48" />
          </div>
          <div>
            <label class="lbl">Цвет</label>
            <input v-model="form.color" class="field" placeholder="Чёрный" />
          </div>
        </div>

        <label class="lbl">Состояние</label>
        <input v-model="form.condition" class="field" placeholder="8/10" />

        <label class="lbl">Описание</label>
        <textarea
          v-model="form.description"
          class="field area"
          rows="3"
          :placeholder="photos.length ? 'Пусто — сгенерируется по фото ✨' : 'Заметки о товаре'"
        />
      </section>

      <!-- Логистика -->
      <section class="block">
        <h2 class="block-title">Закупка и площадка</h2>
        <label class="lbl">Место закупки</label>
        <input v-model="form.purchase_location" class="field" placeholder="Рынок / поставщик" />

        <label class="lbl">Площадка продажи</label>
        <input v-model="form.sales_platform" class="field" placeholder="Avito / Telegram" />

        <label class="lbl">Ссылка на объявление</label>
        <input v-model="form.ad_url" class="field" inputmode="url" placeholder="https://…" />
      </section>

      <!-- Финансы (скрыто для EMPLOYEE) -->
      <section v-if="session.canSeeFinance" class="block">
        <h2 class="block-title">Закупка</h2>
        <label class="lbl">Валюта закупки</label>
        <div class="seg">
          <button
            v-for="c in CURRENCIES"
            :key="c"
            type="button"
            class="seg-opt"
            :class="{ sel: form.cost_currency === c }"
            @click="form.cost_currency = c"
          >
            {{ c }}
          </button>
        </div>

        <label class="lbl">Себестоимость</label>
        <input v-model="form.cost_price" class="field num" inputmode="decimal" placeholder="0" />
        <div class="grid2">
          <div>
            <label class="lbl">Реставрация</label>
            <input v-model="form.restore_cost" class="field num" inputmode="decimal" placeholder="0" />
          </div>
          <div>
            <label class="lbl">Доставка</label>
            <input v-model="form.delivery_cost" class="field num" inputmode="decimal" placeholder="0" />
          </div>
        </div>
      </section>

      <section v-if="session.canSeeFinance" class="block">
        <h2 class="block-title">Цена продажи</h2>
        <label class="lbl">Валюта цены</label>
        <div class="seg">
          <button
            v-for="c in CURRENCIES"
            :key="c"
            type="button"
            class="seg-opt"
            :class="{ sel: form.price_currency === c }"
            @click="form.price_currency = c"
          >
            {{ c }}
          </button>
        </div>

        <label class="lbl">Цена (в объявлении)</label>
        <input v-model="form.list_price" class="field num" inputmode="decimal" placeholder="0" />
        <p class="note">Всё сведётся к основной валюте ({{ session.baseCurrency }}) по курсу НБ РБ.</p>
      </section>

      <div class="scroll-pad" />
    </div>

    <div class="sticky-save">
      <button class="save tap" :disabled="!canSubmit" @click="submit">
        {{ submitting ? 'Сохранение…' : 'Сохранить' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.create {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  flex-direction: column;
  background: var(--ink-0);
  color: var(--fg-0);
}

/* --- Шапка --- */
.head {
  display: flex;
  align-items: center;
  gap: 8.5px;
  padding: calc(var(--safe-top) + 10px) var(--pad) 10px;
  background: var(--ink-0);
}
.close {
  flex: none;
  width: 35px;
  height: 35px;
  min-width: 35px;
  min-height: 35px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--r-pill);
  background: var(--ink-1);
  color: var(--fg-0);
}
.close:active {
  background: var(--ink-2);
}
.close svg {
  width: 15.5px;
  height: 15.5px;
}
.title {
  margin: 0;
  font-size: 15.5px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

/* --- Прокрутка и секции --- */
.scroll {
  flex: 1;
  overflow-y: auto;
  padding: 4px var(--pad) 0;
}
.block {
  padding: 10.5px 0 7px;
}
.block-title {
  margin: 0 0 10.5px;
  font-size: 15px;
  font-weight: 650;
  color: var(--fg-0);
}
.note {
  margin: 8.5px 0 0;
  font-size: 11px;
  line-height: 1.45;
  color: var(--fg-2);
}

/* --- Поля --- */
.lbl {
  display: block;
  margin: 12px 0 5px;
  font-size: 11px;
  color: var(--fg-2);
}
.block-title + .lbl,
.grid2 .lbl {
  margin-top: 0;
}
.field {
  width: 100%;
  height: 43.5px;
  padding: 0 12px;
  border: none;
  border-radius: var(--r-field);
  background: var(--ink-1);
  color: var(--fg-0);
  font-size: 14px;
  outline: none;
  -webkit-appearance: none;
  appearance: none;
}
.field::placeholder {
  color: var(--fg-2);
}
.field:focus {
  background: var(--ink-2);
}
.area {
  height: auto;
  min-height: 90.5px;
  padding: 11.5px 12px;
  line-height: 1.45;
  resize: vertical;
}
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--gap);
}

/* Поле автодополнения — тот же язык, что и у обычных полей. */
:deep(.ac .field) {
  height: 43.5px;
  padding: 0 12px;
  border: none;
  border-radius: var(--r-field);
  background: var(--ink-1);
  color: var(--fg-0);
  font-size: 14px;
}
:deep(.ac .field:focus) {
  background: var(--ink-2);
}
:deep(.ac-list) {
  padding: 5px;
  border: none;
  border-radius: var(--r-field);
  background: var(--ink-2);
  box-shadow: none;
}
:deep(.ac-item) {
  padding: 9.5px 10.5px;
  border-radius: var(--r-sm);
  font-size: 13px;
}
:deep(.ac-item:active) {
  background: var(--ink-3);
}

/* --- Переключатель валюты --- */
.seg {
  display: flex;
  gap: 7px;
}
.seg-opt {
  flex: 1;
  height: 38.5px;
  border-radius: var(--r-pill);
  background: var(--ink-1);
  color: var(--fg-1);
  font-size: 12px;
  font-weight: 650;
}
.seg-opt.sel {
  background: var(--brand);
  color: var(--brand-ink);
}

/* --- Фото: главный актив экрана, крупная сетка --- */
.photo-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8.5px;
}
@media (min-width: 440px) {
  .photo-grid {
    grid-template-columns: repeat(4, 1fr);
  }
}
.thumb {
  position: relative;
  aspect-ratio: 1;
  border-radius: 12px;
  overflow: hidden;
  background: var(--ink-1);
}
.thumb.err {
  outline: 2px solid var(--danger);
  outline-offset: -2px;
}
.thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.thumb-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(14, 15, 18, 0.5);
}
.err-mark {
  font-size: 17.5px;
  font-weight: 700;
  color: #fff;
}
.spinner {
  width: 19px;
  height: 19px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.thumb-x {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 22.5px;
  height: 22.5px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--r-pill);
  background: rgba(14, 15, 18, 0.6);
  color: #fff;
  font-size: 15px;
  line-height: 1;
}
.add-tile {
  aspect-ratio: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border: none;
  border-radius: 12px;
  background: var(--ink-1);
  color: var(--brand);
}
.add-tile:active {
  background: var(--ink-2);
}
.add-label {
  font-size: 11px;
  font-weight: 650;
}
.hidden-input {
  display: none;
}

/* --- Нижняя кнопка --- */
.scroll-pad {
  height: 7px;
}
.sticky-save {
  padding: 10px var(--pad) calc(var(--safe-bottom) + 12px);
  background: var(--ink-0);
}
.save {
  width: 100%;
  height: 43.5px;
  border-radius: var(--r-field);
  background: var(--brand);
  color: var(--brand-ink);
  font-size: 14px;
  font-weight: 650;
}
.save:disabled {
  opacity: 0.45;
}
</style>
