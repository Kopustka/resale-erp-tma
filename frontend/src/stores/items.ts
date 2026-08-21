import { defineStore } from 'pinia'
import { itemsApi } from '@/shared/api/endpoints'
import { ApiError } from '@/shared/api/http'
import type {
  Currency,
  ItemCreate,
  ItemFilters,
  ItemOut,
  ItemStatus,
  ItemUpdate,
} from '@/shared/api/types'
import { nextStatus } from '@/shared/utils/status'
import { STATUS_LABELS } from '@/shared/utils/status'
import { uuid } from '@/shared/utils/uuid'
import { useToastStore } from './toast'

const PAGE_LIMIT = 30
const UNDO_MS = 5000

/**
 * Запросы смены статуса, которые сейчас в полёте, по вещам.
 *
 * Раньше здесь был Set и второй свайп молча отбрасывался — пользователь
 * видел, что жест не сработал. Теперь ждём предыдущий и продолжаем с
 * актуального статуса: двойной свайп честно продвигает на два шага.
 */
const inFlight = new Map<string, Promise<unknown>>()

/** Не чаще одной тихой пересинхронизации в 3 секунды. */
const REFRESH_THROTTLE_MS = 3000
let lastRefresh = 0

interface PendingArchive {
  item: ItemOut
  index: number
  timer: number
}

interface ApplyStatusOptions {
  targetStatus?: ItemStatus
  sellingPrice?: number
  sellingCurrency?: Currency
}

interface ItemsState {
  items: ItemOut[]
  nextCursor: string | null
  loading: boolean
  loadingMore: boolean
  error: string | null
  filters: ItemFilters
  pendingArchives: Record<string, PendingArchive>
  viewArchived: boolean
  /** id вещей, у которых прямо сейчас идёт фоновая AI-генерация. */
  aiPending: Record<string, boolean>
}

let listRequestSeq = 0

export const useItemsStore = defineStore('items', {
  state: (): ItemsState => ({
    items: [],
    nextCursor: null,
    loading: false,
    loadingMore: false,
    error: null,
    filters: { status: null, brand: null, category: null, search: null, ids: null },
    pendingArchives: {},
    viewArchived: false,
    aiPending: {},
  }),

  getters: {
    hasMore(state): boolean {
      return state.nextCursor !== null
    },
    isEmpty(state): boolean {
      return !state.loading && state.items.length === 0
    },
    activeFilterCount(state): number {
      const f = state.filters
      let n = 0
      if (f.status) n++
      if (f.brand) n++
      if (f.category) n++
      if (f.ids && f.ids.length) n++
      return n
    },
  },

  actions: {
    async loadFirst(): Promise<void> {
      const seq = ++listRequestSeq
      this.loading = true
      this.error = null
      try {
        const page = await itemsApi.list({
          ...this.filters,
          limit: PAGE_LIMIT,
          archived: this.viewArchived,
        })
        if (seq !== listRequestSeq) return // устаревший ответ
        this.items = page.items
        this.nextCursor = page.next_cursor
      } catch (e) {
        if (seq !== listRequestSeq) return
        this.error = e instanceof Error ? e.message : 'Ошибка загрузки'
      } finally {
        if (seq === listRequestSeq) this.loading = false
      }
    },

    async loadMore(): Promise<void> {
      if (this.loadingMore || this.loading || this.nextCursor === null) return
      this.loadingMore = true
      try {
        const page = await itemsApi.list({
          ...this.filters,
          cursor: this.nextCursor,
          limit: PAGE_LIMIT,
          archived: this.viewArchived,
        })
        this.items.push(...page.items)
        this.nextCursor = page.next_cursor
      } catch (e) {
        useToastStore().error(e instanceof Error ? e.message : 'Ошибка подгрузки')
      } finally {
        this.loadingMore = false
      }
    },

    /** Полностью заменить фильтры и перезагрузить. */
    async setFilters(patch: Partial<ItemFilters>): Promise<void> {
      this.filters = { ...this.filters, ...patch }
      await this.loadFirst()
    },

    async resetFilters(): Promise<void> {
      this.filters = { status: null, brand: null, category: null, search: null, ids: null }
      await this.loadFirst()
    },

    async setSearch(search: string): Promise<void> {
      // Смена поиска сбрасывает drill-down по ids.
      await this.setFilters({ search: search || null, ids: null })
    },

    /**
     * Оптимистичная смена статуса (свайп вправо / ручной переход).
     * Мгновенно меняем в сторе, PATCH с Idempotency-Key + version.
     * При ошибке/409 — откат к прежнему состоянию и Toast; при 409 перезагружаем айтем.
     */
    async applyStatus(item: ItemOut, opts: ApplyStatusOptions = {}): Promise<boolean> {
      const toast = useToastStore()

      // Ждём предыдущую смену по этой же вещи, иначе уйдём со старой
      // версией и получим 409 на ровном месте.
      const pending = inFlight.get(item.id)
      if (pending) await pending.catch(() => undefined)

      const idx = this.items.findIndex((i) => i.id === item.id)
      if (idx === -1) return false
      const current = this.items[idx]

      // Статус мог уйти вперёд, пока ждали, — цель считаем от свежего.
      const target = opts.targetStatus ?? nextStatus(current.status)
      if (!target) {
        toast.show({ message: 'Нет следующего статуса', kind: 'info' })
        return false
      }

      const snapshot = {
        status: current.status,
        version: current.version,
        selling_price: current.selling_price,
        sold_date: current.sold_date,
      }

      // Оптимистика.
      current.status = target
      if (opts.sellingPrice !== undefined) current.selling_price = opts.sellingPrice

      let release: () => void = () => undefined
      inFlight.set(item.id, new Promise<void>((r) => (release = r)))
      try {
        const send = (version: number, key: string) =>
          itemsApi.patchStatus(
            item.id,
            {
              target_status: target,
              version,
              selling_price: opts.sellingPrice,
              selling_currency: opts.sellingCurrency,
            },
            key,
          )

        let fresh: ItemOut
        try {
          fresh = await send(snapshot.version, uuid())
        } catch (e) {
          // Версия устарела (правку сделали в карточке или в другой вкладке).
          // Это не повод терять действие пользователя: перечитываем вещь и
          // повторяем один раз с актуальной версией.
          if (!(e instanceof ApiError && e.isConflict)) throw e
          const actual = await itemsApi.get(item.id)
          if (actual.status === target) {
            const at = this.items.findIndex((i) => i.id === item.id)
            if (at !== -1) this.items.splice(at, 1, actual)
            return true  // кто-то уже перевёл в нужный статус — цель достигнута
          }
          fresh = await send(actual.version, uuid())
        }

        // Заменяем на серверную версию (актуальные version, net_profit, roi и т.д.).
        const at = this.items.findIndex((i) => i.id === item.id)
        if (at !== -1) this.items.splice(at, 1, fresh)
        return true
      } catch (e) {
        // Откат.
        const at = this.items.findIndex((i) => i.id === item.id)
        if (at !== -1) {
          const it = this.items[at]
          it.status = snapshot.status
          it.version = snapshot.version
          it.selling_price = snapshot.selling_price
          it.sold_date = snapshot.sold_date
        }

        // Сообщение сервера точнее любого локального: там и про цену,
        // и про недопустимый переход.
        if (e instanceof ApiError && e.isConflict) {
          toast.error('Данные разошлись — обновил карточку, повторите')
          await this.reloadItem(item.id)
        } else {
          toast.error(e instanceof Error ? e.message : 'Не удалось сменить статус')
        }
        return false
      } finally {
        inFlight.delete(item.id)
        release()
      }
    },

    /**
     * Тихая пересинхронизация первой страницы: обновляет уже показанные
     * карточки на месте, не сбрасывая прокрутку. Если состав изменился
     * (появились или исчезли вещи) — перезагружает список целиком.
     *
     * Нужна при возврате в мини-апп: пока пользователь был в чате с ботом
     * (подтверждал предпросмотр, отвечал в комментариях), данные могли уйти
     * вперёд, и раньше это лечилось только перезагрузкой страницы.
     */
    async refresh(): Promise<void> {
      if (this.loading) return
      const now = Date.now()
      if (now - lastRefresh < REFRESH_THROTTLE_MS) return
      lastRefresh = now
      try {
        const page = await itemsApi.list({
          limit: PAGE_LIMIT,
          archived: this.viewArchived,
          ...this.filters,
        })
        const known = new Set(this.items.map((i) => i.id))
        const headChanged = page.items.some((i) => !known.has(i.id))
        if (headChanged) {
          await this.loadFirst()
          return
        }
        for (const fresh of page.items) {
          const at = this.items.findIndex((i) => i.id === fresh.id)
          if (at !== -1) this.items.splice(at, 1, fresh)
        }
      } catch {
        /* сеть моргнула — оставляем что было, не мешаем работе */
      }
    },

    async reloadItem(id: string): Promise<void> {
      try {
        const fresh = await itemsApi.get(id)
        const at = this.items.findIndex((i) => i.id === id)
        if (at !== -1) this.items.splice(at, 1, fresh)
      } catch {
        /* если удалён/недоступен — оставляем как есть */
      }
    },

    /**
     * Архивация (свайп влево) с окном отмены ~5с.
     * Сразу убираем из списка, реальный DELETE шлём только если не отменили.
     */
    archiveWithUndo(item: ItemOut): void {
      const toast = useToastStore()
      const index = this.items.findIndex((i) => i.id === item.id)
      if (index === -1) return
      const [removed] = this.items.splice(index, 1)

      const timer = window.setTimeout(() => {
        void this.commitArchive(removed.id)
      }, UNDO_MS)

      this.pendingArchives[removed.id] = { item: removed, index, timer }

      toast.show({
        message: `«${removed.sku}» в архив`,
        kind: 'info',
        actionLabel: 'Отменить',
        duration: UNDO_MS,
        onAction: () => this.undoArchive(removed.id),
      })
    },

    undoArchive(id: string): void {
      const pending = this.pendingArchives[id]
      if (!pending) return
      clearTimeout(pending.timer)
      const insertAt = Math.min(pending.index, this.items.length)
      this.items.splice(insertAt, 0, pending.item)
      delete this.pendingArchives[id]
    },

    async commitArchive(id: string): Promise<void> {
      const pending = this.pendingArchives[id]
      if (!pending) return
      delete this.pendingArchives[id]
      try {
        await itemsApi.archive(id)
      } catch (e) {
        // Не удалось — возвращаем в список.
        const insertAt = Math.min(pending.index, this.items.length)
        this.items.splice(insertAt, 0, pending.item)
        useToastStore().error(e instanceof Error ? e.message : 'Не удалось архивировать')
      }
    },

    /** Переключение между активными и архивом. */
    async setArchivedView(v: boolean): Promise<void> {
      if (this.viewArchived === v) return
      this.viewArchived = v
      this.nextCursor = null
      await this.loadFirst()
    },

    /** Редактирование полей товара. */
    async updateItem(id: string, patch: ItemUpdate): Promise<ItemOut> {
      const fresh = await itemsApi.update(id, patch)
      const at = this.items.findIndex((i) => i.id === id)
      if (at !== -1) this.items.splice(at, 1, fresh)
      return fresh
    },

    /** Немедленная архивация (из детали, без окна отмены). */
    async archiveNow(id: string): Promise<void> {
      await itemsApi.archive(id)
      const at = this.items.findIndex((i) => i.id === id)
      if (at !== -1) this.items.splice(at, 1)
    },

    /** Достать из архива. В архивном списке — убрать из выдачи. */
    async restore(id: string): Promise<void> {
      await itemsApi.restore(id)
      if (this.viewArchived) {
        const at = this.items.findIndex((i) => i.id === id)
        if (at !== -1) this.items.splice(at, 1)
      }
    },

    /** Безвозвратное удаление. */
    async hardDelete(id: string): Promise<void> {
      await itemsApi.remove(id)
      const at = this.items.findIndex((i) => i.id === id)
      if (at !== -1) this.items.splice(at, 1)
    },

    async create(payload: ItemCreate): Promise<ItemOut> {
      const created = await itemsApi.create(payload)
      // Показываем новинку сверху, если она подходит под текущие фильтры (упрощённо — добавляем).
      this.items.unshift(created)
      return created
    },

    /**
     * Трекинг фоновой AI-генерации: помечает вещь как «генерируется»
     * (индикатор в списке), поллит сервер и обновляет карточку, когда
     * название/описание доехали. baselineTitle — заглушка на момент создания.
     */
    async trackAiGeneration(
      id: string,
      baselineTitle: string,
      needTitle: boolean,
      needDescr: boolean,
    ): Promise<void> {
      this.aiPending[id] = true
      const delays = [3000, 4000, 5000, 7000, 9000, 12000]
      try {
        for (const d of delays) {
          await new Promise((r) => window.setTimeout(r, d))
          let fresh
          try {
            fresh = await itemsApi.get(id)
          } catch {
            return // вещь удалили/недоступна — прекращаем
          }
          const at = this.items.findIndex((i) => i.id === id)
          if (at !== -1) this.items.splice(at, 1, fresh)
          const titleDone = !needTitle || fresh.title !== baselineTitle
          const descrDone = !needDescr || !!(fresh.description && fresh.description.trim())
          if (titleDone && descrDone) {
            useToastStore().success('✨ Название и описание сгенерированы')
            return
          }
        }
      } finally {
        delete this.aiPending[id]
      }
    },

    labelFor(status: ItemStatus): string {
      return STATUS_LABELS[status]
    },
  },
})
