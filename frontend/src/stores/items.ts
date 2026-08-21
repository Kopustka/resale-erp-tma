import { defineStore } from 'pinia'
import { itemsApi } from '@/shared/api/endpoints'
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

/** Цепочка запросов по каждой вещи: следующий стартует после предыдущего. */
const chain = new Map<string, Promise<boolean>>()

/**
 * Сколько шагов по вещи ещё не подтверждено сервером. Нужен, чтобы ответ
 * на первый свайп не затёр статус, который пользователь уже насвайпал
 * дальше.
 */
const pendingSteps = new Map<string, number>()

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
    /**
     * Меняет статус. Отрисовка мгновенная, сеть — отдельно.
     *
     * Раньше запрос ждали до показа результата, и быстрые свайпы выглядели
     * так, будто не сработали. Теперь статус меняется в списке сразу, а
     * запросы уходят строго по очереди на вещь: каждый следующий берёт
     * версию, которую вернул предыдущий, иначе сервер ответит 409.
     */
    async applyStatus(item: ItemOut, opts: ApplyStatusOptions = {}): Promise<boolean> {
      const toast = useToastStore()
      const idx = this.items.findIndex((i) => i.id === item.id)
      if (idx === -1) return false
      const current = this.items[idx]

      // Цель считаем от локального статуса: он уже включает предыдущие
      // свайпы, ответ по которым ещё не пришёл.
      const target = opts.targetStatus ?? nextStatus(current.status)
      if (!target) {
        toast.show({ message: 'Нет следующего статуса', kind: 'info' })
        return false
      }

      // 1. Показываем сразу, до всякой сети.
      current.status = target
      if (opts.sellingPrice !== undefined) current.selling_price = opts.sellingPrice
      pendingSteps.set(item.id, (pendingSteps.get(item.id) ?? 0) + 1)

      // 2. Запросы — цепочкой, чтобы версии не разъехались.
      const prev = chain.get(item.id) ?? Promise.resolve(true)
      const run = prev.then(() => this.sendStatus(item.id, target, opts))
      chain.set(
        item.id,
        run.catch(() => false),
      )
      return run
    },

    /** Один шаг цепочки: отправка и сверка с ответом сервера. */
    async sendStatus(
      id: string,
      target: ItemStatus,
      opts: ApplyStatusOptions,
    ): Promise<boolean> {
      const toast = useToastStore()
      const at = this.items.findIndex((i) => i.id === id)
      if (at === -1) {
        pendingSteps.delete(id)
        return false
      }

      try {
        const fresh = await itemsApi.patchStatus(
          id,
          {
            target_status: target,
            // Версия актуальная: предыдущий шаг цепочки её обновил.
            version: this.items[at].version,
            selling_price: opts.sellingPrice,
            selling_currency: opts.sellingCurrency,
          },
          uuid(),
        )
        this.mergeFresh(id, fresh)
        return true
      } catch (e) {
        // Сверяемся с сервером, а не откатываемся на локальный снимок: он
        // мог устареть, и список расходился с действительностью — вещь
        // показывалась в одном статусе, а на сервере была в другом.
        await this.reloadItem(id)
        chain.delete(id)
        pendingSteps.delete(id)
        toast.error(e instanceof Error ? e.message : 'Не удалось сменить статус')
        return false
      } finally {
        const left = (pendingSteps.get(id) ?? 1) - 1
        if (left > 0) pendingSteps.set(id, left)
        else pendingSteps.delete(id)
      }
    },

    /**
     * Кладёт ответ сервера в список, не затирая более свежие свайпы.
     *
     * Пока в очереди есть неотправленные шаги, локальный статус новее
     * ответа: если подставить ответ целиком, карточка прыгнет назад.
     */
    mergeFresh(id: string, fresh: ItemOut): void {
      const at = this.items.findIndex((i) => i.id === id)
      if (at === -1) return
      const local = this.items[at]
      const stillPending = (pendingSteps.get(id) ?? 0) > 1
      this.items.splice(at, 1, {
        ...fresh,
        status: stillPending ? local.status : fresh.status,
        selling_price: stillPending ? local.selling_price : fresh.selling_price,
      })
    },

    /**
     * Тихая пересинхронизация первой страницы: обновляет уже показанные
     * карточки на месте, не сбрасывая прокрутку. Если состав изменился —
     * перезагружает список целиком.
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
        if (page.items.some((i) => !known.has(i.id))) {
          await this.loadFirst()
          return
        }
        // Вещи с неподтверждёнными свайпами не трогаем: ответ сервера по
        // ним ещё в пути, и подстановка списка вернула бы старый статус.
        for (const fresh of page.items) {
          if (pendingSteps.has(fresh.id)) continue
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
