<script setup lang="ts">
/**
 * Скидка на вещь: быстрые проценты, ручная цена, публикация сразу или
 * по расписанию. Один компонент на два места — карточку вещи и
 * контент-план, поэтому вещь можно и передать снаружи, и выбрать здесь.
 */
import { computed, ref, watch } from 'vue'
import BottomSheet from '@/shared/ui/BottomSheet.vue'
import { discountsApi, itemsApi } from '@/shared/api/endpoints'
import { ApiError } from '@/shared/api/http'
import { useItemsStore } from '@/stores/items'
import { useToastStore } from '@/stores/toast'
import { hapticImpact, hapticSelection } from '@/shared/telegram/webapp'
import {
  applyPercent,
  percentOf,
  QUICK_PERCENTS,
  roundPrice,
  stepFor,
} from '@/shared/utils/discount'
import { CURRENCY_SYMBOLS } from '@/shared/api/types'
import type { Currency, ItemOut } from '@/shared/api/types'

const props = defineProps<{
  modelValue: boolean
  /** Вещь задана снаружи (карточка). null — выбираем здесь (контент-план). */
  item: ItemOut | null
}>()

const emit = defineEmits<{
  'update:modelValue': [boolean]
  created: []
}>()

const items = useItemsStore()
const toast = useToastStore()

const picked = ref<ItemOut | null>(null)
const search = ref('')
const found = ref<ItemOut[]>([])
const searching = ref(false)

const priceInput = ref('')
const when = ref('')
const saving = ref(false)

const target = computed(() => props.item ?? picked.value)
const oldPrice = computed(() => target.value?.list_price ?? null)
const currency = computed<Currency>(
  () => (target.value?.price_currency ?? 'BYN') as Currency,
)
const symbol = computed(() => CURRENCY_SYMBOLS[currency.value])

const newPrice = computed(() => {
  const n = Number(priceInput.value.replace(/\s/g, '').replace(',', '.'))
  return Number.isFinite(n) && priceInput.value.trim() !== '' ? n : null
})

const percent = computed(() =>
  oldPrice.value && newPrice.value ? percentOf(oldPrice.value, newPrice.value) : 0,
)

/** Причина, по которой сохранять нельзя. null — можно. */
const problem = computed<string | null>(() => {
  if (!target.value) return 'Выберите вещь'
  if (oldPrice.value === null || oldPrice.value === undefined)
    return 'У вещи нет цены — сначала укажите её'
  if (newPrice.value === null) return 'Укажите цену со скидкой'
  if (newPrice.value <= 0) return 'Цена должна быть больше нуля'
  if (newPrice.value >= oldPrice.value)
    return `Должна быть меньше текущей — ${oldPrice.value} ${symbol.value}`
  return null
})

const minWhen = computed(() => {
  const d = new Date()
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset())
  return d.toISOString().slice(0, 16)
})

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    picked.value = props.item
    priceInput.value = ''
    when.value = ''
    search.value = ''
    found.value = []
  },
)

function setPercent(p: number): void {
  if (oldPrice.value === null || oldPrice.value === undefined) return
  hapticSelection()
  priceInput.value = String(applyPercent(oldPrice.value, p, currency.value))
}

let searchTimer: number | null = null
watch(search, (q) => {
  if (searchTimer !== null) window.clearTimeout(searchTimer)
  const text = q.trim()
  if (text.length < 2) {
    found.value = []
    return
  }
  searchTimer = window.setTimeout(async () => {
    searching.value = true
    try {
      const page = await itemsApi.list({ search: text, limit: 10 })
      found.value = page.items
    } catch {
      found.value = []
    } finally {
      searching.value = false
    }
  }, 300)
})

function pick(it: ItemOut): void {
  hapticSelection()
  picked.value = it
  found.value = []
  search.value = ''
  priceInput.value = ''
}

async function submit(): Promise<void> {
  if (problem.value || saving.value || !target.value || newPrice.value === null) return
  saving.value = true
  try {
    // datetime-local отдаёт местное время без зоны — переводим явно,
    // иначе сервер прочитает его как UTC и скидка уйдёт не тогда.
    const iso = when.value ? new Date(when.value).toISOString() : null
    await discountsApi.create(
      target.value.id,
      roundPrice(newPrice.value, currency.value),
      iso,
    )
    hapticImpact('medium')
    toast.success(iso ? 'Скидка запланирована' : 'Скидка объявлена в канале')
    if (!iso) await items.reloadItem(target.value.id)
    emit('created')
    emit('update:modelValue', false)
  } catch (e) {
    const msg =
      e instanceof ApiError ? e.message : e instanceof Error ? e.message : 'Не вышло'
    toast.error(msg)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <BottomSheet
    :model-value="modelValue"
    title="Скидка"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="body">
      <!-- Выбор вещи нужен только в контент-плане -->
      <template v-if="!props.item">
        <label class="lbl">Вещь</label>
        <div v-if="picked" class="picked">
          <span class="picked-name">{{ picked.sku }} · {{ picked.title || picked.brand }}</span>
          <button class="link tap" @click="picked = null">Сменить</button>
        </div>
        <template v-else>
          <input v-model="search" class="field" placeholder="Артикул, бренд или название" />
          <p v-if="searching" class="hint">Ищу…</p>
          <ul v-else-if="found.length" class="found">
            <li v-for="f in found" :key="f.id">
              <button class="found-row tap" @click="pick(f)">
                <span>{{ f.sku }} · {{ f.title || f.brand }}</span>
                <span class="hint">{{ f.list_price ?? '—' }}</span>
              </button>
            </li>
          </ul>
        </template>
      </template>

      <template v-if="target">
        <div class="now">
          Текущая цена: <b>{{ oldPrice ?? '—' }} {{ symbol }}</b>
        </div>

        <label class="lbl">Быстро</label>
        <div class="quick">
          <button
            v-for="p in QUICK_PERCENTS"
            :key="p"
            class="quick-btn tap"
            :disabled="oldPrice === null || oldPrice === undefined"
            @click="setPercent(p)"
          >
            −{{ p }}%
          </button>
        </div>

        <label class="lbl">Новая цена</label>
        <input v-model="priceInput" class="field" inputmode="decimal" placeholder="0" />
        <p v-if="newPrice !== null && percent > 0" class="hint">
          Скидка {{ percent }}% · было {{ oldPrice }} {{ symbol }}, станет
          {{ roundPrice(newPrice, currency) }} {{ symbol }}
        </p>

        <label class="lbl">Когда объявить</label>
        <input v-model="when" type="datetime-local" class="field" :min="minWhen" />
        <p class="note">Пусто — объявим сразу, ответом на пост вещи в канале.</p>
        <p v-if="stepFor(currency) > 1" class="note">
          Цена округляется вниз до {{ stepFor(currency) }} {{ symbol }} — чтобы
          в ценнике не было единиц.
        </p>

        <p v-if="problem" class="note warn">{{ problem }}</p>

        <div class="row">
          <button class="btn-primary tap" :disabled="!!problem || saving" @click="submit">
            {{ saving ? '…' : when ? 'Запланировать' : 'Объявить сейчас' }}
          </button>
          <button class="btn-secondary tap" @click="emit('update:modelValue', false)">
            Отмена
          </button>
        </div>
      </template>
    </div>
  </BottomSheet>
</template>

<style scoped>
.body {
  padding: 4px 0 8px;
}
.lbl {
  display: block;
  font-size: 12px;
  color: var(--tg-theme-hint-color);
  margin: 12px 0 4px;
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
.now {
  font-size: 14px;
  margin-top: 4px;
}
.quick {
  display: flex;
  gap: var(--gap);
}
.quick-btn {
  flex: 1;
  min-height: var(--tap);
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
  font-size: 15px;
  font-weight: 700;
}
.quick-btn:disabled {
  opacity: 0.5;
}
.hint {
  font-size: 12px;
  color: var(--tg-theme-hint-color);
  margin: 6px 0 0;
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
.picked {
  display: flex;
  align-items: center;
  gap: 10px;
}
.picked-name {
  flex: 1;
  font-size: 14px;
  font-weight: 700;
}
.found {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
}
.found-row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  min-height: var(--tap);
  padding: 8px 10px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  margin-bottom: 6px;
  text-align: left;
  font-size: 14px;
}
.link {
  color: var(--tg-theme-link-color);
  font-weight: 700;
  min-height: var(--tap);
}
.row {
  display: flex;
  gap: var(--gap);
  margin-top: 16px;
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
</style>
