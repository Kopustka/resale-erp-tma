<script setup lang="ts">
/**
 * Настройка формы вещи.
 *
 * Магазины ведут разный товар: одному нужны замеры рукава, другому номер
 * лота, третьему хватает названия и цены. Здесь магазин собирает форму под
 * себя — прячет лишнее, переименовывает под свой язык, добавляет своё.
 *
 * Порядок меняется кнопками, а не перетаскиванием: перетаскивание в списке,
 * который сам прокручивается, на телефоне попадает мимо и конфликтует со
 * скроллом. Кнопка вверх-вниз скучнее, но срабатывает всегда.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { closeOverlay } from '@/app/navigation'
import { fieldsApi } from '@/shared/api/endpoints'
import { ApiError } from '@/shared/api/http'
import { useToastStore } from '@/stores/toast'
import { hapticImpact, hapticSelection } from '@/shared/telegram/webapp'
import type { FieldKind, FormField } from '@/shared/api/types'

const toast = useToastStore()

const fields = ref<FormField[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const dirty = ref(false)

const addOpen = ref(false)
const newLabel = ref('')
const newKind = ref<FieldKind>('TEXT')
const newHint = ref('')
const newOptions = ref('')
const adding = ref(false)
const confirmId = ref<string | null>(null)

const KINDS: { key: FieldKind; label: string }[] = [
  { key: 'TEXT', label: 'Текст' },
  { key: 'TEXTAREA', label: 'Абзац' },
  { key: 'NUMBER', label: 'Число' },
  { key: 'MONEY', label: 'Сумма' },
  { key: 'SELECT', label: 'Выбор' },
]
const KIND_LABEL: Record<FieldKind, string> = {
  TEXT: 'Текст',
  TEXTAREA: 'Абзац',
  NUMBER: 'Число',
  MONEY: 'Сумма',
  SELECT: 'Выбор',
}

const shownCount = computed(() => fields.value.filter((f) => f.enabled).length)

function msg(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.message || fallback
  return e instanceof Error ? e.message : fallback
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    fields.value = await fieldsApi.list()
  } catch (e) {
    error.value = msg(e, 'Не удалось загрузить поля')
  } finally {
    loading.value = false
  }
}
onMounted(load)

function toggle(f: FormField): void {
  if (f.locked) {
    toast.show({ message: `«${f.label}» нельзя скрыть — без него вещь не завести`, kind: 'info' })
    return
  }
  hapticSelection()
  f.enabled = !f.enabled
  dirty.value = true
}

function toggleRequired(f: FormField): void {
  // Скрыть и «сделать необязательным» — разные запреты. Бренд из формы не
  // убрать (из него собирается название), но вещь без бренда существует.
  if (f.required_locked) {
    toast.show({ message: `«${f.label}» всегда обязательно`, kind: 'info' })
    return
  }
  hapticSelection()
  f.required = !f.required
  dirty.value = true
}

function rename(f: FormField, value: string, el?: EventTarget | null): void {
  const v = value.trim()
  if (!v) {
    // Пустое имя не принимаем, но и в поле его не оставляем: f.label не
    // менялся, поэтому Vue не перерисовывал input, и человек видел поле
    // без названия, хотя сохранилось бы старое.
    if (el instanceof HTMLInputElement) el.value = f.label
    return
  }
  if (v === f.label) return
  f.label = v
  dirty.value = true
}

/** Крестик при несохранённых правках спрашивает, а не выбрасывает их. */
const confirmClose = ref(false)
let closeTimer: ReturnType<typeof setTimeout> | null = null

function tryClose(): void {
  if (!dirty.value || confirmClose.value) {
    if (closeTimer) clearTimeout(closeTimer)
    closeOverlay()
    return
  }
  confirmClose.value = true
  toast.show({ message: 'Правки не сохранены. Нажмите ещё раз, чтобы выйти', kind: 'info' })
  if (closeTimer) clearTimeout(closeTimer)
  closeTimer = setTimeout(() => (confirmClose.value = false), 4000)
}

onBeforeUnmount(() => {
  if (closeTimer) clearTimeout(closeTimer)
})

function move(index: number, delta: number): void {
  const to = index + delta
  if (to < 0 || to >= fields.value.length) return
  hapticSelection()
  const list = fields.value
  ;[list[index], list[to]] = [list[to], list[index]]
  dirty.value = true
}

async function save(): Promise<void> {
  if (saving.value) return
  saving.value = true
  try {
    // Позицию проставляем по текущему порядку списка: сервер хранит число,
    // а человек двигал строки.
    fields.value = await fieldsApi.save(
      fields.value.map((f, i) => ({
        id: f.id,
        label: f.label,
        enabled: f.enabled,
        required: f.required,
        position: i,
      })),
    )
    dirty.value = false
    hapticImpact('medium')
    toast.success('Форма сохранена')
  } catch (e) {
    toast.error(msg(e, 'Не удалось сохранить'))
  } finally {
    saving.value = false
  }
}

async function addField(): Promise<void> {
  const label = newLabel.value.trim()
  if (!label || adding.value) return
  adding.value = true
  try {
    const created = await fieldsApi.create({
      label,
      kind: newKind.value,
      hint: newHint.value.trim() || null,
      options:
        newKind.value === 'SELECT'
          ? newOptions.value.split(',').map((o) => o.trim()).filter(Boolean)
          : [],
    })
    fields.value.push(created)
    newLabel.value = ''
    newHint.value = ''
    newOptions.value = ''
    newKind.value = 'TEXT'
    addOpen.value = false
    hapticImpact('medium')
    toast.success(`Поле «${created.label}» добавлено`)
  } catch (e) {
    toast.error(msg(e, 'Не удалось добавить поле'))
  } finally {
    adding.value = false
  }
}

async function remove(f: FormField): Promise<void> {
  try {
    await fieldsApi.remove(f.id)
    fields.value = fields.value.filter((x) => x.id !== f.id)
    confirmId.value = null
    toast.success('Поле удалено')
  } catch (e) {
    toast.error(msg(e, 'Не удалось удалить'))
  }
}
</script>

<template>
  <div class="fields">
    <header class="head">
      <button class="close" aria-label="Закрыть" @click="tryClose">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
             stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
          <path d="m6 6 12 12M18 6 6 18" />
        </svg>
      </button>
      <h1 class="title">Поля карточки</h1>
      <button v-if="dirty" class="save-top" :disabled="saving" @click="save">
        {{ saving ? '…' : 'Готово' }}
      </button>
    </header>

    <div class="scroll">
      <p v-if="loading" class="state">Загрузка…</p>
      <p v-else-if="error" class="state neg">{{ error }}</p>

      <template v-else>
        <p class="lead">
          Что спрашивать при добавлении вещи. Выключенное не показывается в форме,
          но уже введённые значения остаются. Сейчас показывается {{ shownCount }}.
        </p>

        <ul class="list">
          <li v-for="(f, i) in fields" :key="f.id" class="row" :class="{ off: !f.enabled }">
            <div class="order">
              <button class="ord hit" :disabled="i === 0" aria-label="Выше" @click="move(i, -1)">
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
                     stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m6 15 6-6 6 6" /></svg>
              </button>
              <button
                class="ord hit"
                :disabled="i === fields.length - 1"
                aria-label="Ниже"
                @click="move(i, 1)"
              >
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
                     stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6" /></svg>
              </button>
            </div>

            <div class="body">
              <input
                class="name"
                :value="f.label"
                maxlength="60"
                :aria-label="`Название поля ${f.label}`"
                @change="rename(f, ($event.target as HTMLInputElement).value, $event.target)"
              />
              <div class="meta">
                <span class="kind">{{ KIND_LABEL[f.kind] }}</span>
                <button class="req" :class="{ on: f.required }" @click="toggleRequired(f)">
                  {{ f.required ? 'обязательное' : 'необязательное' }}
                </button>
                <span v-if="!f.builtin" class="own">своё</span>
              </div>
            </div>

            <div class="tail">
              <button
                class="sw"
                :class="{ on: f.enabled }"
                :aria-label="f.enabled ? 'Скрыть поле' : 'Показать поле'"
                @click="toggle(f)"
              >
                <span class="knob" />
              </button>
              <template v-if="!f.builtin">
                <button v-if="confirmId === f.id" class="del on" @click="remove(f)">Точно</button>
                <button v-else class="del" aria-label="Удалить поле" @click="confirmId = f.id">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
                       stroke-width="2" stroke-linecap="round" aria-hidden="true">
                    <path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13" />
                  </svg>
                </button>
              </template>
            </div>
          </li>
        </ul>

        <section class="add">
          <template v-if="addOpen">
            <label class="lbl">Название поля</label>
            <input v-model="newLabel" class="field" placeholder="Например, номер лота" maxlength="60" />

            <label class="lbl">Тип</label>
            <div class="kinds">
              <button
                v-for="k in KINDS"
                :key="k.key"
                class="kind-opt"
                :class="{ sel: newKind === k.key }"
                @click="newKind = k.key"
              >
                {{ k.label }}
              </button>
            </div>

            <template v-if="newKind === 'SELECT'">
              <label class="lbl">Варианты через запятую</label>
              <input v-model="newOptions" class="field" placeholder="Лето, Зима, Демисезон" />
            </template>

            <label class="lbl">Подсказка под полем</label>
            <input v-model="newHint" class="field" placeholder="Необязательно" maxlength="120" />

            <div class="row-btn">
              <button class="primary" :disabled="!newLabel.trim() || adding" @click="addField">
                {{ adding ? '…' : 'Добавить' }}
              </button>
              <button class="secondary" @click="addOpen = false">Отмена</button>
            </div>
          </template>
          <button v-else class="secondary wide" @click="addOpen = true">+ Своё поле</button>
        </section>

        <button v-if="dirty" class="primary wide sticky" :disabled="saving" @click="save">
          {{ saving ? 'Сохраняю…' : 'Сохранить порядок и названия' }}
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.fields {
  position: fixed;
  inset: 0;
  z-index: 120;
  display: flex;
  flex-direction: column;
  background: var(--ink-0);
  color: var(--fg-0);
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: calc(var(--safe-top) + 9px) var(--pad) 9px;
}
.close {
  width: 35px;
  height: 35px;
  border-radius: 50%;
  background: var(--ink-2);
  color: var(--fg-1);
  display: grid;
  place-items: center;
}
.title {
  flex: 1;
  margin: 0;
  font-size: 17px;
  font-weight: 650;
  letter-spacing: -0.01em;
}
.save-top {
  color: var(--brand);
  font-size: 14px;
  font-weight: 650;
  padding: 6px 4px;
}
.scroll {
  flex: 1;
  overflow-y: auto;
  padding: 4px var(--pad) calc(var(--safe-bottom) + 24px);
}
.state {
  padding: 22px 0;
  text-align: center;
  color: var(--fg-1);
}
.neg {
  color: var(--danger);
}
.lead {
  margin: 0 0 14px;
  font-size: 12.5px;
  line-height: 17px;
  color: var(--fg-1);
}
.list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.row {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 10px;
  border-radius: var(--r-card);
  background: var(--ink-1);
}
.row.off .name,
.row.off .meta {
  opacity: 0.45;
}
.order {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ord {
  width: 26px;
  height: 20px;
  border-radius: 7px;
  background: var(--ink-2);
  color: var(--fg-1);
  display: grid;
  place-items: center;
}
.ord:disabled {
  opacity: 0.3;
}
.body {
  flex: 1;
  min-width: 0;
}
.name {
  width: 100%;
  border: 0;
  padding: 0;
  background: none;
  color: var(--fg-0);
  /* 16px обязателен: на меньшем iOS зумит страницу при фокусе. */
  font-size: 16px;
  font-weight: 650;
  letter-spacing: -0.01em;
}
.meta {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 2px;
  font-size: 11px;
}
.kind {
  color: var(--fg-2);
}
.req {
  color: var(--fg-2);
  font-size: 11px;
  font-weight: 650;
}
.req.on {
  color: var(--s-prep-ink);
}
.own {
  padding: 2px 6px;
  border-radius: var(--r-pill);
  background: color-mix(in srgb, var(--brand) 16%, transparent);
  color: var(--brand);
  font-weight: 700;
}
.tail {
  display: flex;
  align-items: center;
  gap: 7px;
}
.sw {
  width: 40px;
  height: 24px;
  padding: 2px;
  border-radius: var(--r-pill);
  background: var(--ink-3);
  display: flex;
  align-items: center;
  transition: background 0.15s ease;
}
.sw.on {
  background: var(--brand);
  justify-content: flex-end;
}
.knob {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #fff;
}
.del {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: var(--ink-2);
  color: var(--danger);
  display: grid;
  place-items: center;
}
.del.on {
  width: auto;
  padding: 0 10px;
  background: var(--danger);
  color: #fff;
  font-size: 11.5px;
  font-weight: 700;
}
.add {
  margin-top: 18px;
}
.lbl {
  display: block;
  margin: 12px 0 5px;
  font-size: 12.5px;
  color: var(--fg-2);
}
.field {
  width: 100%;
  height: 44px;
  padding: 0 12px;
  border: 0;
  border-radius: var(--r-field);
  background: var(--ink-1);
  color: var(--fg-0);
  font-size: 16px;
}
.field::placeholder {
  color: var(--fg-2);
}
.kinds {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.kind-opt {
  padding: 7px 12px;
  border-radius: var(--r-pill);
  background: var(--ink-1);
  color: var(--fg-1);
  font-size: 12.5px;
  font-weight: 650;
}
.kind-opt.sel {
  background: var(--brand);
  color: var(--brand-ink);
}
.row-btn {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
.primary,
.secondary {
  height: 44px;
  padding: 0 18px;
  border-radius: var(--r-field);
  font-size: 14px;
  font-weight: 650;
}
.primary {
  background: var(--brand);
  color: var(--brand-ink);
}
.primary:disabled {
  opacity: 0.5;
}
.secondary {
  background: var(--ink-2);
  color: var(--fg-0);
}
.wide {
  width: 100%;
}
.sticky {
  margin-top: 18px;
}
</style>
