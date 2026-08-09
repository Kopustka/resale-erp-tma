<script setup lang="ts">
/**
 * Редактор шаблона поста: название, тело с палитрой плейсхолдеров
 * и живое превью (рендерит сервер, HTML прогоняем через санитайзер).
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import BottomSheet from '@/shared/ui/BottomSheet.vue'
import { useTemplatesStore } from '@/stores/templates'
import { useToastStore } from '@/stores/toast'
import { hapticNotify, hapticSelection } from '@/shared/telegram/webapp'
import { sanitizeTelegramHtml } from '@/shared/utils/sanitize'
import type { PostTemplate } from '@/shared/api/types'

const props = defineProps<{
  modelValue: boolean
  /** null — создание нового шаблона. */
  template: PostTemplate | null
  /** Стартовое тело для нового шаблона. */
  initialBody?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [boolean]
  saved: [PostTemplate]
}>()

const templates = useTemplatesStore()
const toast = useToastStore()

const PREVIEW_DEBOUNCE_MS = 400

const name = ref('')
const body = ref('')
const bodyInput = ref<HTMLTextAreaElement | null>(null)

const previewHtml = ref('')
const previewError = ref<string | null>(null)
const previewLoading = ref(false)

let previewTimer: number | null = null
let previewSeq = 0

const isEdit = computed(() => props.template !== null)
const canSave = computed(
  () => name.value.trim().length > 0 && body.value.trim().length > 0 && !templates.saving,
)

function close(): void {
  emit('update:modelValue', false)
}

function stopPreviewTimer(): void {
  if (previewTimer !== null) {
    clearTimeout(previewTimer)
    previewTimer = null
  }
}

/** Дёргает сервер за отрендеренной подписью; последний ответ выигрывает. */
async function runPreview(source: string): Promise<void> {
  const seq = ++previewSeq
  previewLoading.value = true
  try {
    const caption = await templates.preview(source)
    if (seq !== previewSeq) return
    previewHtml.value = sanitizeTelegramHtml(caption)
    previewError.value = null
  } catch (e) {
    if (seq !== previewSeq) return
    previewHtml.value = ''
    previewError.value = e instanceof Error ? e.message : 'Не удалось построить превью'
  } finally {
    if (seq === previewSeq) previewLoading.value = false
  }
}

function schedulePreview(): void {
  stopPreviewTimer()
  const source = body.value
  if (!source.trim()) {
    previewSeq++
    previewHtml.value = ''
    previewError.value = null
    previewLoading.value = false
    return
  }
  previewTimer = window.setTimeout(() => {
    previewTimer = null
    void runPreview(source)
  }, PREVIEW_DEBOUNCE_MS)
}

watch(body, schedulePreview)

watch(
  () => props.modelValue,
  (open) => {
    if (!open) {
      stopPreviewTimer()
      previewSeq++
      return
    }
    void templates.fetchPlaceholders()
    name.value = props.template?.name ?? ''
    body.value = props.template?.body ?? props.initialBody ?? ''
    previewHtml.value = ''
    previewError.value = null
    schedulePreview()
  },
)

/** Вставляет {key} в позицию курсора, а не в конец текста. */
function insertPlaceholder(key: string): void {
  const token = `{${key}}`
  const el = bodyInput.value
  hapticSelection()
  if (!el) {
    body.value += token
    return
  }
  const start = el.selectionStart ?? body.value.length
  const end = el.selectionEnd ?? start
  body.value = body.value.slice(0, start) + token + body.value.slice(end)
  const caret = start + token.length
  void nextTick(() => {
    el.focus()
    el.setSelectionRange(caret, caret)
  })
}

async function save(): Promise<void> {
  if (!canSave.value) return
  const payloadName = name.value.trim()
  const payloadBody = body.value.trim()
  const saved = props.template
    ? await templates.update(props.template.id, { name: payloadName, body: payloadBody })
    : await templates.create(payloadName, payloadBody)
  if (!saved) {
    hapticNotify('error')
    return
  }
  hapticNotify('success')
  toast.success(props.template ? 'Шаблон обновлён' : 'Шаблон создан')
  emit('saved', saved)
  close()
}

onBeforeUnmount(stopPreviewTimer)
</script>

<template>
  <BottomSheet
    :model-value="modelValue"
    :title="isEdit ? 'Изменить шаблон' : 'Новый шаблон'"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <label class="lbl">Название</label>
    <input
      v-model="name"
      class="field"
      placeholder="Напр. Основной"
      autocomplete="off"
      maxlength="60"
    />

    <label class="lbl">Тело шаблона</label>
    <textarea
      ref="bodyInput"
      v-model="body"
      class="field area"
      rows="6"
      placeholder="{brand} {title}&#10;Размер: {size}&#10;Цена: {price}"
    />

    <div v-if="templates.placeholders.length" class="ph-block">
      <div class="ph-title hint">Плейсхолдеры — тап вставляет в текст</div>
      <div class="chips">
        <button
          v-for="p in templates.placeholders"
          :key="p.key"
          class="chip"
          type="button"
          :title="p.example"
          @click="insertPlaceholder(p.key)"
        >
          {{ p.label }}
        </button>
      </div>
    </div>

    <div class="preview-block">
      <div class="preview-head">
        <span class="ph-title hint">Превью на демо-данных</span>
        <span v-if="previewLoading" class="ph-title hint">…</span>
      </div>
      <div v-if="previewError" class="preview-err negative">{{ previewError }}</div>
      <!-- eslint-disable-next-line vue/no-v-html — прогнали через sanitizeTelegramHtml -->
      <div v-else-if="previewHtml" class="preview-body" v-html="previewHtml" />
      <div v-else class="preview-empty hint">Напишите текст — покажем, как выйдет пост.</div>
    </div>

    <div class="actions">
      <button class="btn btn-secondary tap" type="button" @click="close">Отмена</button>
      <button class="btn btn-primary tap" type="button" :disabled="!canSave" @click="save">
        {{ templates.saving ? 'Сохранение…' : 'Сохранить' }}
      </button>
    </div>
  </BottomSheet>
</template>

<style scoped>
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
  min-height: 132px;
  line-height: 1.45;
}
.ph-block {
  margin-top: 12px;
}
.ph-title {
  font-size: 12px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.chip {
  min-height: 36px;
  padding: 8px 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-link-color);
  font-size: 13px;
  font-weight: 600;
}
.preview-block {
  margin-top: 16px;
}
.preview-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.preview-body {
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  font-size: 14px;
  line-height: 1.45;
  white-space: pre-wrap;
  word-break: break-word;
}
.preview-body :deep(a) {
  color: var(--tg-theme-link-color);
}
.preview-body :deep(code),
.preview-body :deep(pre) {
  font-family: ui-monospace, Menlo, monospace;
  font-size: 13px;
}
.preview-body :deep(pre) {
  margin: 4px 0;
  white-space: pre-wrap;
}
.preview-body :deep(blockquote) {
  margin: 4px 0;
  padding-left: 10px;
  border-left: 2px solid var(--tg-theme-hint-color);
}
.preview-body :deep(tg-spoiler) {
  background: var(--tg-theme-hint-color);
  color: transparent;
  border-radius: 3px;
}
.preview-err,
.preview-empty {
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  font-size: 13px;
}
.actions {
  display: flex;
  gap: 10px;
  margin: 18px 0 4px;
}
.btn {
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
  opacity: 0.5;
}
.btn-secondary {
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
}
</style>
