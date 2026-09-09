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

/** Цепочка запросов по каждой вещи: следующий стартует после предыдущего. */
const chain = new Map<string, Promise<boolean>>()

/**
 * Сколько шагов по вещи ещё не подтверждено сервером. Нужен, чтобы ответ
 * на первый переход не затёр статус, который пользователь уже пролистал
 * дальше.
 */
const pendingSteps = new Map<string, number>()

/**
 * Обновляет вещь НА МЕСТЕ, не подменяя объект.
 *
 * Это не косметика. Список рисуется виртуальным окном, и оно пересобирается
 * по прокрутке и изменению размеров, а не на каждую замену элемента массива.
 * Когда обновление приходило через splice, объект в окне оставался прежним:
 * данные в сторе уже новые, а карточка показывала старый статус и старую
 * цену, пока список не тронешь пальцем. Правка того же объекта будит
 * зависимости самой карточки, и она перерисовывается сразу.
 */
function patchInPlace(target: ItemOut, fresh: Partial<ItemOut>): void {
  Object.assign(target, fresh)
}

/** Не чаще одной тихой пересинхронизации в 3 секунды. */
const REFRESH_THROTTLE_MS = 3000
let lastRefresh = 0

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
      // Тот же счётчик, что и в loadFirst. Без него страница, запрошенная до
      // смены фильтра, дописывалась в уже перерисованный список: под
      // результатами поиска оказывались посторонние вещи, а курсор указывал
      // в старую выдачу — дальше подгружалось ещё больше чужого.
      const seq = ++listRequestSeq
      this.loadingMore = true
      try {
        const page = await itemsApi.list({
          ...this.filters,
          cursor: this.nextCursor,
          limit: PAGE_LIMIT,
          archived: this.viewArchived,
        })
        if (seq !== listRequestSeq) return
        this.items.push(...page.items)
        this.nextCursor = page.next_cursor
      } catch (e) {
        if (seq !== listRequestSeq) return
        useToastStore().error(e instanceof Error ? e.message : 'Ошибка подгрузки')
      } finally {
        if (seq === listRequestSeq) this.loadingMore = false
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
     * Меняет статус: кнопка в строке списка или переход в карточке товара.
     * Отрисовка мгновенная, сеть — отдельно.
     *
     * Раньше запрос ждали до показа результата, и частые нажатия выглядели
     * так, будто не сработали. Теперь статус меняется в списке сразу, а
     * запросы уходят строго по очереди на вещь: каждый следующий берёт
     * версию, которую вернул предыдущий, иначе сервер ответит 409.
     *
     * При ошибке состояние не откатывается на локальный снимок, а
     * перечитывается с сервера — см. sendStatus.
     */
    async applyStatus(item: ItemOut, opts: ApplyStatusOptions = {}): Promise<boolean> {
      const toast = useToastStore()
      const idx = this.items.findIndex((i) => i.id === item.id)
      if (idx === -1) return false
      const current = this.items[idx]

      // Цель считаем от локального статуса: он уже включает предыдущие
      // переходы, ответ по которым ещё не пришёл.
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
      const link = run.catch(() => false)
      chain.set(item.id, link)
      // Убираем звено, когда очередь по этой вещи опустела. Раньше запись
      // жила до перезагрузки мини-аппа: по одной на каждую тронутую вещь.
      void link.then(() => {
        if (chain.get(item.id) === link) chain.delete(item.id)
      })
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
     * Кладёт ответ сервера в список, не затирая более свежие переходы.
     *
     * Пока в очереди есть неотправленные шаги, локальный статус новее
     * ответа: если подставить ответ целиком, карточка прыгнет назад.
     */
    mergeFresh(id: string, fresh: ItemOut): void {
      const at = this.items.findIndex((i) => i.id === id)
      if (at === -1) return
      const local = this.items[at]
      const stillPending = (pendingSteps.get(id) ?? 0) > 1
      patchInPlace(local, {
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
        // Вещи с неподтверждёнными переходами не трогаем: ответ сервера по
        // ним ещё в пути, и подстановка списка вернула бы старый статус.
        for (const fresh of page.items) {
          if (pendingSteps.has(fresh.id)) continue
          const at = this.items.findIndex((i) => i.id === fresh.id)
          if (at !== -1) patchInPlace(this.items[at], fresh)
        }
      } catch {
        /* сеть моргнула — оставляем что было, не мешаем работе */
      }
    },

    async reloadItem(id: string): Promise<void> {
      try {
        const fresh = await itemsApi.get(id)
        const at = this.items.findIndex((i) => i.id === id)
        if (at !== -1) patchInPlace(this.items[at], fresh)
      } catch {
        /* если удалён/недоступен — оставляем как есть */
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
      try {
        const fresh = await itemsApi.update(id, patch)
        const at = this.items.findIndex((i) => i.id === id)
        if (at !== -1) patchInPlace(this.items[at], fresh)
        return fresh
      } catch (e) {
        // Карточку успел изменить кто-то другой. Подтягиваем свежие данные
        // и говорим об этом прямо: сохранять поверх чужой правки вслепую
        // хуже, чем попросить человека посмотреть, что изменилось.
        if (e instanceof ApiError && e.isConflict) {
          await this.reloadItem(id)
          throw new Error('Карточку изменили в другом месте — данные обновлены, проверьте и сохраните ещё раз')
        }
        throw e
      }
    },

    /** Архивация из карточки товара. */
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
          if (at !== -1) patchInPlace(this.items[at], fresh)
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
