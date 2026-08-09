<script setup lang="ts">
/**
 * Шаблоны постов: список, выбор активного, создание/правка/удаление
 * и копирование дизайна с чужого поста через бота.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { closeOverlay } from '@/app/navigation'
import TemplateEditorSheet from '@/components/TemplateEditorSheet.vue'
import { useTemplatesStore } from '@/stores/templates'
import { useToastStore } from '@/stores/toast'
import { hapticImpact, hapticSelection, openTelegramLink } from '@/shared/telegram/webapp'
import type { PostTemplate } from '@/shared/api/types'

const templates = useTemplatesStore()
const toast = useToastStore()

const editorOpen = ref(false)
const editing = ref<PostTemplate | null>(null)
const confirmDeleteId = ref<string | null>(null)

const waitingHint = computed(() =>
  templates.captureStatus === 'armed'
    ? 'Бот ждёт ваш пример поста'
    : 'Откройте чат с ботом и пришлите пример',
)

onMounted(() => {
  void templates.fetch()
  void templates.fetchPlaceholders()
})

onBeforeUnmount(() => {
  // Уходим с экрана — снимаем сессию, иначе бот продолжит ждать пример.
  if (templates.capturing) templates.cancelCapture()
  templates.clearHighlight()
})

async function startCapture(): Promise<void> {
  hapticImpact('medium')
  const link = await templates.startCapture()
  if (link) openTelegramLink(link)
}

function cancelCapture(): void {
  hapticSelection()
  templates.cancelCapture()
}

function openNew(): void {
  hapticSelection()
  editing.value = null
  editorOpen.value = true
}

function openEdit(tpl: PostTemplate): void {
  hapticSelection()
  editing.value = tpl
  editorOpen.value = true
}

async function selectTemplate(tpl: PostTemplate): Promise<void> {
  if (tpl.is_default) return
  hapticSelection()
  if (await templates.makeDefault(tpl.id)) toast.success(`Активен: ${tpl.name}`)
}

function askDelete(id: string): void {
  hapticSelection()
  confirmDeleteId.value = confirmDeleteId.value === id ? null : id
}

async function doDelete(tpl: PostTemplate): Promise<void> {
  confirmDeleteId.value = null
  if (await templates.remove(tpl.id)) {
    hapticImpact('medium')
    toast.success('Шаблон удалён')
  }
}

/** Короткий фрагмент тела для карточки — без разметки и лишних переносов. */
function excerpt(body: string): string {
  const plain = body.replace(/<[^>]+>/g, '').replace(/\s*\n\s*/g, ' · ').trim()
  return plain.length > 90 ? `${plain.slice(0, 90)}…` : plain
}

function onSaved(): void {
  editorOpen.value = false
}
</script>

<template>
  <div class="templates">
    <header class="head">
      <button class="close tap" aria-label="Закрыть" @click="closeOverlay">
        <svg viewBox="0 0 24 24" width="24" height="24">
          <path fill="currentColor" d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" />
        </svg>
      </button>
      <h1 class="title">Шаблоны постов</h1>
    </header>

    <div class="scroll no-scrollbar">
      <!-- Копирование дизайна -->
      <section class="block">
        <template v-if="templates.capturing">
          <div class="waiting">
            <span class="spinner" aria-hidden="true" />
            <div class="waiting-text">
              <p class="waiting-title">{{ waitingHint }}</p>
              <p class="note">
                Перешлите боту пост, оформление которого нравится. Нейросеть
                разберёт структуру и соберёт из неё шаблон.
              </p>
            </div>
          </div>
          <div class="waiting-actions">
            <button
              v-if="templates.captureDeepLink"
              class="btn-secondary tap"
              @click="openTelegramLink(templates.captureDeepLink)"
            >
              Открыть чат
            </button>
            <button class="btn-secondary tap" @click="cancelCapture">Отменить</button>
          </div>
        </template>

        <button v-else class="btn-primary tap" :disabled="templates.captureStarting" @click="startCapture">
          ✨ Скопировать дизайн из поста
        </button>
        <p v-if="!templates.capturing" class="note">
          Пришлите боту любой понравившийся пост — он повторит его оформление.
        </p>
      </section>

      <!-- Список -->
      <section class="block">
        <h2 class="block-title">Мои шаблоны</h2>

        <p v-if="templates.loading" class="hint">Загрузка…</p>
        <p v-else-if="templates.error" class="negative">{{ templates.error }}</p>

        <div v-else-if="templates.isEmpty" class="empty">
          <p>Шаблонов пока нет.</p>
          <p class="note">
            Посты выходят в стандартном оформлении: название, описание, цена и замеры.
            Создайте шаблон или скопируйте дизайн с чужого поста.
          </p>
        </div>

        <ul v-else class="list">
          <li
            v-for="tpl in templates.list"
            :key="tpl.id"
            class="card"
            :class="{ active: tpl.is_default, fresh: templates.highlightId === tpl.id }"
          >
            <button class="card-main tap" @click="selectTemplate(tpl)">
              <div class="card-head">
                <span class="card-name">{{ tpl.name }}</span>
                <span v-if="tpl.is_default" class="badge">Активный</span>
              </div>
              <p class="card-excerpt">{{ excerpt(tpl.body) }}</p>
            </button>

            <div class="card-actions">
              <button class="link tap" @click="openEdit(tpl)">Изменить</button>
              <template v-if="confirmDeleteId === tpl.id">
                <button class="link negative tap" @click="doDelete(tpl)">Точно удалить</button>
                <button class="link tap" @click="confirmDeleteId = null">Нет</button>
              </template>
              <button v-else class="link negative tap" @click="askDelete(tpl.id)">Удалить</button>
            </div>

            <p v-if="confirmDeleteId === tpl.id && templates.list.length === 1" class="note warn">
              Это последний шаблон — постинг вернётся к стандартному оформлению.
            </p>
          </li>
        </ul>

        <button class="btn-secondary tap add" @click="openNew">+ Создать шаблон</button>
      </section>
    </div>

    <TemplateEditorSheet
      v-model="editorOpen"
      :template="editing"
      @saved="onSaved"
    />
  </div>
</template>

<style scoped>
.templates {
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
  padding: 8px 16px calc(var(--safe-bottom) + 24px);
}
.block {
  padding: 12px 0;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.block:last-child {
  border-bottom: none;
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
  color: var(--tg-theme-hint-color);
  margin: 8px 0 0;
  line-height: 1.4;
}
.note.warn {
  color: var(--accent-negative);
}
.hint {
  color: var(--tg-theme-hint-color);
}
.negative {
  color: var(--accent-negative);
}

/* Кнопки */
.btn-primary,
.btn-secondary {
  width: 100%;
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
.add {
  margin-top: 12px;
}

/* Ожидание примера поста */
.waiting {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.waiting-text {
  flex: 1;
}
.waiting-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
}
.waiting-actions {
  display: flex;
  gap: var(--gap);
  margin-top: 12px;
}
.spinner {
  flex: none;
  width: 20px;
  height: 20px;
  margin-top: 2px;
  border: 2px solid var(--tg-theme-secondary-bg-color);
  border-top-color: var(--tg-theme-link-color);
  border-radius: 50%;
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@media (prefers-reduced-motion: reduce) {
  .spinner {
    animation-duration: 2.4s;
  }
}

/* Список шаблонов */
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--gap);
}
.card {
  background: var(--tg-theme-secondary-bg-color);
  border-radius: var(--radius);
  padding: 12px;
  border: 1px solid transparent;
  transition: border-color 0.2s ease;
}
.card.active {
  border-color: var(--tg-theme-link-color);
}
.card.fresh {
  border-color: var(--accent-positive);
}
.card-main {
  display: block;
  width: 100%;
  text-align: left;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-name {
  font-size: 15px;
  font-weight: 700;
}
.badge {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--tg-theme-link-color);
  color: var(--tg-theme-button-text-color);
}
.card-excerpt {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.4;
  color: var(--tg-theme-hint-color);
}
.card-actions {
  display: flex;
  gap: 16px;
  margin-top: 10px;
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
</style>
