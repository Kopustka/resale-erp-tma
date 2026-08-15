<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
import AutocompleteInput from '@/components/AutocompleteInput.vue'
import { useItemsStore } from '@/stores/items'
import { useSessionStore } from '@/stores/session'
import { useToastStore } from '@/stores/toast'
import { closeOverlay, setTab } from '@/app/navigation'
import { hapticImpact, hapticNotify, openTelegramLink } from '@/shared/telegram/webapp'
import type { ItemCreate } from '@/shared/api/types'
import { itemsApi, mediaApi } from '@/shared/api/endpoints'
import { CURRENCIES, type Currency } from '@/shared/api/types'
import type { VoiceParseResult } from '@/shared/api/types'

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
  for (const file of files) {
    photos.value.push({
      id: null,
      preview: URL.createObjectURL(file),
      uploading: true,
      error: false,
    })
    // берём реактивный прокси из массива (мутировать исходный объект нельзя)
    const entry = photos.value[photos.value.length - 1]
    try {
      const { photo_id } = await mediaApi.upload(file)
      entry.id = photo_id
    } catch (err) {
      entry.error = true
      toast.error(err instanceof Error ? err.message : 'Не удалось загрузить фото')
    } finally {
      entry.uploading = false
    }
  }
}

function removePhoto(idx: number): void {
  const [removed] = photos.value.splice(idx, 1)
  if (removed) URL.revokeObjectURL(removed.preview)
}

// ------------------------------ Голосовой ввод ------------------------------ //
/** Записывать можно там, где есть MediaRecorder и доступ к микрофону. */
const speechSupported =
  typeof window !== 'undefined' &&
  typeof MediaRecorder !== 'undefined' &&
  !!navigator.mediaDevices?.getUserMedia
const recognizing = ref(false)
const parsing = ref(false)
const interim = ref('')
const manualPhrase = ref('')
const showCheat = ref(false)

/**
 * Запись голоса прямо в мини-аппе.
 *
 * Раньше здесь был Web Speech API: он сам распознавал речь, но в WebView
 * Telegram недоступен или ведёт себя непредсказуемо. Теперь пишем звук
 * через MediaRecorder и отдаём его нейросети — она и распознаёт, и
 * раскладывает по полям. Браузеру остаётся только запись.
 */
let media: MediaRecorder | null = null
let chunks: Blob[] = []
let stream: MediaStream | null = null
let stopTimer: number | null = null

/** Предохранитель: если про запись забыли, глушим её сами. */
const MAX_RECORDING_MS = 120_000

/** Формат зависит от платформы: Chrome даёт webm, Safari — mp4. */
function pickMime(): string {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg']
  for (const t of candidates) {
    if (MediaRecorder.isTypeSupported?.(t)) return t
  }
  return ''
}

function releaseStream(): void {
  stream?.getTracks().forEach((t) => t.stop())
  stream = null
}

function clearStopTimer(): void {
  if (stopTimer !== null) {
    window.clearTimeout(stopTimer)
    stopTimer = null
  }
}

async function startRecording(): Promise<void> {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch {
    toast.error('Нет доступа к микрофону — разрешите его или надиктуйте боту')
    return
  }
  const mime = pickMime()
  try {
    media = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
  } catch {
    releaseStream()
    toast.error('Запись недоступна — надиктуйте боту')
    return
  }
  chunks = []
  media.ondataavailable = (e) => {
    if (e.data && e.data.size) chunks.push(e.data)
  }
  media.onstop = () => {
    releaseStream()
    const blob = new Blob(chunks, { type: media?.mimeType || 'audio/webm' })
    chunks = []
    media = null
    recognizing.value = false
    // Меньше половины секунды — это случайное касание, а не фраза.
    if (blob.size < 1200) {
      interim.value = ''
      return
    }
    void sendRecording(blob)
  }
  recognizing.value = true
  interim.value = ''
  hapticImpact('medium')
  media.start()
  stopTimer = window.setTimeout(() => {
    if (recognizing.value) toggleRecording()
  }, MAX_RECORDING_MS)
}

/** Нажатие: первое — начать запись, второе — закончить и разобрать. */
function toggleRecording(): void {
  if (parsing.value) return
  if (recognizing.value) {
    hapticImpact('light')
    clearStopTimer()
    try {
      media?.stop()
    } catch {
      releaseStream()
      recognizing.value = false
    }
    return
  }
  void startRecording()
}

/** Отправляет запись нейросети и заполняет поля. */
async function sendRecording(blob: Blob): Promise<void> {
  parsing.value = true
  try {
    const r = await itemsApi.voiceUpload(blob)
    applyFields(r)
    hapticNotify(r.low_confidence ? 'warning' : 'success')
    toast.success(
      r.low_confidence ? 'Заполнил что расслышал — проверь поля' : 'Поля заполнены',
    )
  } catch (e) {
    hapticNotify('error')
    toast.error(e instanceof Error ? e.message : 'Не удалось разобрать запись')
  } finally {
    parsing.value = false
  }
}

// ---------------- Диктовка боту (надёжный путь без браузера) ---------------- //
const dictating = ref(false)
let dictateToken: string | null = null
let dictateTimer: number | null = null
let dictateDeadline = 0

function stopDictatePolling(): void {
  if (dictateTimer !== null) {
    window.clearInterval(dictateTimer)
    dictateTimer = null
  }
}

function cancelDictation(): void {
  stopDictatePolling()
  dictating.value = false
  const t = dictateToken
  dictateToken = null
  if (t) void itemsApi.cancelVoiceCapture(t).catch(() => undefined)
}

/**
 * Открывает чат с ботом, где можно записать голосовое штатной кнопкой.
 * Распознаёт и раскладывает по полям та же модель — браузерный Web Speech
 * API здесь не участвует, поэтому путь работает в любом клиенте.
 */
async function startDictation(): Promise<void> {
  if (dictating.value) return
  hapticImpact('medium')
  try {
    const s = await itemsApi.startVoiceCapture()
    dictateToken = s.token
    dictating.value = true
    dictateDeadline = Date.now() + Math.min(s.expires_in * 1000, 300_000)
    openTelegramLink(s.deep_link)
    dictateTimer = window.setInterval(() => void pollDictation(), 2000)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось начать диктовку')
  }
}

async function pollDictation(): Promise<void> {
  const token = dictateToken
  if (!token) return stopDictatePolling()
  if (Date.now() > dictateDeadline) {
    cancelDictation()
    toast.error('Время ожидания истекло')
    return
  }
  try {
    const st = await itemsApi.voiceCaptureStatus(token)
    if (st.status === 'waiting' || st.status === 'armed') return
    stopDictatePolling()
    dictateToken = null
    dictating.value = false
    if (st.status === 'done' && st.fields) {
      applyFields(st.fields)
      hapticNotify('success')
      toast.success(st.transcript ? `Услышал: «${st.transcript}»` : 'Поля заполнены')
    } else {
      hapticNotify('error')
      toast.error(st.error || 'Не удалось разобрать запись')
    }
  } catch {
    /* сеть моргнула — попробуем на следующем тике */
  }
}

/** Раскладывает разобранные поля по форме. Общее для обоих путей. */
function applyFields(r: VoiceParseResult): void {
  if (r.brand) form.brand = r.brand
  if (r.category) form.category = r.category
  if (r.size) form.size = r.size
  if (r.color) form.color = r.color
  if (r.condition) form.condition = r.condition
  if (r.title && !form.title.trim()) form.title = r.title
  if (session.canSeeFinance && r.cost_price != null) form.cost_price = String(r.cost_price)
  if (r.list_price != null) form.list_price = String(r.list_price)
}

/** Разбирает фразу на сервере и предзаполняет поля формы. */
async function applyVoice(text: string): Promise<void> {
  const phrase = text.trim()
  if (!phrase || parsing.value) return
  parsing.value = true
  try {
    const r = await itemsApi.parseVoice(phrase)
    applyFields(r)
    manualPhrase.value = ''
    hapticNotify(r.low_confidence ? 'warning' : 'success')
    toast.success(
      r.low_confidence ? 'Заполнил что расслышал — проверь поля' : 'Поля заполнены — проверь и сохрани',
    )
  } catch (e) {
    hapticNotify('error')
    toast.error(e instanceof Error ? e.message : 'Не удалось разобрать фразу')
  } finally {
    parsing.value = false
  }
}

onBeforeUnmount(() => {
  cancelDictation()
  clearStopTimer()
  try {
    media?.stop()
  } catch {
    /* уже остановлен */
  }
  releaseStream()
  for (const p of photos.value) URL.revokeObjectURL(p.preview)
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
      <!-- Голосовой ввод -->
      <section class="block voice-block">
        <div class="voice-row">
          <button
            v-if="speechSupported"
            class="mic"
            :class="{ live: recognizing }"
            :aria-label="recognizing ? 'Остановить запись' : 'Заполнить голосом'"
            @click="toggleRecording"
          >
            <svg v-if="recognizing" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
              <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" />
            </svg>
            <svg v-else viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">
              <path fill="currentColor" d="M12 3a3 3 0 0 1 3 3v5a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zm-7 8a7 7 0 0 0 6 6.9V21H8v2h8v-2h-3v-3.1A7 7 0 0 0 19 11h-2a5 5 0 0 1-10 0H5z" />
            </svg>
          </button>
          <div class="voice-text">
            <div class="voice-title">Заполнить голосом</div>
            <div class="voice-hint hint">
              <span v-if="recognizing">Записываю… нажми ещё раз, когда закончишь</span>
              <span v-else-if="parsing">Разбираю…</span>
              <span v-else-if="speechSupported">Нажми и наговори вещь одной фразой</span>
              <span v-else>Запись недоступна — надиктуй боту или впиши фразу</span>
            </div>
            <div v-if="interim" class="voice-interim">{{ interim }}</div>
          </div>
        </div>

        <!-- Запасной путь: показываем, только если записать в приложении нельзя -->
        <button
          v-if="!dictating && !speechSupported"
          class="dictate tap"
          :disabled="parsing"
          @click="startDictation"
        >
          🎤 Надиктовать боту
        </button>
        <div v-else-if="dictating" class="dictate-wait">
          <span class="spinner" aria-hidden="true" />
          <span class="hint">Жду голосовое в чате с ботом…</span>
          <button class="link tap" @click="cancelDictation">Отменить</button>
        </div>

        <!-- Фолбэк вводом текста (нет распознавания речи) -->
        <div v-if="!speechSupported" class="inline">
          <input
            v-model="manualPhrase"
            class="field"
            placeholder="Напр. Найк худи размер эль чёрный закупка сорок"
            @keyup.enter="applyVoice(manualPhrase)"
          />
          <button class="add-btn tap" :disabled="parsing" @click="applyVoice(manualPhrase)">
            Разобрать
          </button>
        </div>

        <button class="cheat-toggle" type="button" @click="showCheat = !showCheat">
          {{ showCheat ? 'Скрыть подсказку' : 'Что говорить? Чек-лист фраз' }}
        </button>
        <div v-if="showCheat" class="cheat">
          <div class="cheat-ex">
            «Найк худи размер эль чёрный состояние восемь из десяти закупка сорок»
          </div>
          <ul class="cheat-list">
            <li><b>Бренд:</b> найк, адидас, прада, стон, кархарт…</li>
            <li><b>Тип:</b> худи, куртка, джинсы, футболка, кроссовки…</li>
            <li><b>Размер:</b> «размер эль» или «размер сорок восемь»</li>
            <li><b>Цвет:</b> чёрный, синий, бежевый, серый…</li>
            <li><b>Состояние:</b> «состояние восемь из десяти»</li>
            <li v-if="session.canSeeFinance"><b>Закупка:</b> «закупка сорок» / «купил за сто двадцать»</li>
          </ul>
          <p class="cheat-note hint">Порядок слов любой. Что не расслышит — впишешь руками.</p>
        </div>
      </section>

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
        <p class="hint note">Из галереи или камеры телефона. Можно несколько.</p>
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
        <select v-model="form.cost_currency" class="field">
          <option v-for="c in CURRENCIES" :key="c" :value="c">{{ c }}</option>
        </select>
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

        <h2 class="block-title" style="margin-top: 16px">Цена продажи</h2>
        <label class="lbl">Валюта цены</label>
        <select v-model="form.price_currency" class="field">
          <option v-for="c in CURRENCIES" :key="c" :value="c">{{ c }}</option>
        </select>
        <label class="lbl">Цена (в объявлении)</label>
        <input v-model="form.list_price" class="field num" inputmode="decimal" placeholder="0" />
        <p class="hint note">Всё сведётся к основной валюте ({{ session.baseCurrency }}) по курсу НБ РБ.</p>
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
  font-size: 18px;
  font-weight: 700;
  margin: 0;
}
.scroll {
  flex: 1;
  overflow-y: auto;
  padding: 8px 16px;
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
.note {
  font-size: 12px;
  margin: 0 0 10px;
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
.field:focus {
  border-color: var(--tg-theme-link-color);
}
.area {
  resize: vertical;
  min-height: 76px;
}
.grid2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.photo-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
@media (max-width: 360px) {
  .photo-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}
.thumb {
  position: relative;
  aspect-ratio: 1;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--tg-theme-secondary-bg-color);
}
.thumb.err {
  outline: 2px solid var(--tg-theme-destructive-text-color, #e53935);
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
  background: rgba(0, 0, 0, 0.35);
}
.err-mark {
  font-weight: 800;
  font-size: 20px;
  color: #fff;
}
.thumb-x {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: 16px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
.add-tile {
  aspect-ratio: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  border-radius: var(--radius-sm);
  border: 1px dashed var(--tg-theme-hint-color);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-link-color);
}
.add-label {
  font-size: 11px;
  font-weight: 600;
}
.spinner {
  width: 20px;
  height: 20px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.hidden-input {
  display: none;
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

/* --- Голосовой ввод --- */
.voice-block {
  background: var(--tg-theme-secondary-bg-color);
  border-radius: var(--radius);
  padding: 12px;
  margin-top: 4px;
}
.voice-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.mic {
  flex: none;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
  display: flex;
  align-items: center;
  justify-content: center;
  /* Было none под жест удержания: с обычным нажатием это только мешало
     начать прокрутку пальцем с кнопки. */
  touch-action: manipulation;
  transition: transform 0.1s ease;
}
.mic:active {
  transform: scale(0.94);
}
.mic.live {
  background: var(--accent-negative);
  color: #fff;
  animation: mic-pulse 1s ease-in-out infinite;
}
@keyframes mic-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(255, 59, 48, 0.5);
  }
  50% {
    box-shadow: 0 0 0 12px rgba(255, 59, 48, 0);
  }
}
.voice-text {
  min-width: 0;
}
.voice-title {
  font-size: 15px;
  font-weight: 700;
}
.voice-hint {
  font-size: 12px;
  margin-top: 2px;
}
.voice-interim {
  font-size: 13px;
  margin-top: 4px;
  color: var(--tg-theme-text-color);
}
.cheat-toggle {
  margin-top: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--tg-theme-link-color);
}
.cheat {
  margin-top: 8px;
}
.cheat-ex {
  font-size: 13px;
  font-style: italic;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--tg-theme-bg-color);
  margin-bottom: 8px;
}
.cheat-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.5;
}
.cheat-note {
  font-size: 11px;
  margin: 8px 0 0;
}
.inline {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.inline .field {
  flex: 1;
}
.add-btn {
  flex: none;
  padding: 0 14px;
  border-radius: var(--radius);
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
  font-weight: 700;
}
.add-btn:disabled {
  opacity: 0.5;
}
.dictate {
  width: 100%;
  min-height: var(--tap);
  margin-top: var(--gap);
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
  font-size: 15px;
  font-weight: 700;
}
.dictate:disabled {
  opacity: 0.6;
}
.dictate-wait {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: var(--gap);
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
}
.dictate-wait .hint {
  flex: 1;
  font-size: 13px;
}
.dictate-wait .link {
  color: var(--tg-theme-link-color);
  font-weight: 700;
  min-height: var(--tap);
}
.spinner {
  flex: none;
  width: 18px;
  height: 18px;
  border: 2px solid var(--tg-theme-bg-color);
  border-top-color: var(--tg-theme-link-color);
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
