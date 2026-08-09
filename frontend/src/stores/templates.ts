import { defineStore } from 'pinia'
import { templatesApi } from '@/shared/api/endpoints'
import { ApiError } from '@/shared/api/http'
import type {
  CaptureState,
  PostTemplate,
  TemplatePlaceholder,
} from '@/shared/api/types'
import { useToastStore } from './toast'

/** Период опроса статуса «скопировать дизайн из поста». */
const POLL_MS = 2500
/** Предельное время ожидания примера поста. */
const POLL_TIMEOUT_MS = 180_000
/** Сколько держим подсветку только что добавленного шаблона. */
const HIGHLIGHT_MS = 5000

interface TemplatesState {
  list: PostTemplate[]
  placeholders: TemplatePlaceholder[]
  loading: boolean
  saving: boolean
  generating: boolean
  error: string | null
  /** id шаблона, который подсвечиваем в списке (после капчи/создания). */
  highlightId: string | null
  // --- сессия «скопировать дизайн из поста» ---
  captureToken: string | null
  captureDeepLink: string | null
  captureStatus: CaptureState | null
  captureError: string | null
  captureStarting: boolean
}

// Таймеры держим вне state: реактивность им не нужна, а утечки — тем более.
let pollTimer: number | null = null
let pollDeadline = 0
let pollInflight = false
let highlightTimer: number | null = null

export const useTemplatesStore = defineStore('templates', {
  state: (): TemplatesState => ({
    list: [],
    placeholders: [],
    loading: false,
    saving: false,
    generating: false,
    error: null,
    highlightId: null,
    captureToken: null,
    captureDeepLink: null,
    captureStatus: null,
    captureError: null,
    captureStarting: false,
  }),

  getters: {
    /** Активный (используемый ботом) шаблон. */
    activeTemplate(state): PostTemplate | null {
      return state.list.find((t) => t.is_default) ?? null
    },
    isEmpty(state): boolean {
      return !state.loading && state.list.length === 0
    },
    /** Идёт ожидание примера поста: ссылка выдана либо бот уже принял /start. */
    capturing(state): boolean {
      return state.captureStatus === 'waiting' || state.captureStatus === 'armed'
    },
  },

  actions: {
    async fetch(): Promise<void> {
      this.loading = true
      this.error = null
      try {
        this.list = await templatesApi.list()
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Не удалось загрузить шаблоны'
      } finally {
        this.loading = false
      }
    },

    async fetchPlaceholders(): Promise<void> {
      if (this.placeholders.length) return
      try {
        this.placeholders = await templatesApi.placeholders()
      } catch {
        /* палитра плейсхолдеров необязательна — молча живём без неё */
      }
    },

    async create(name: string, body: string): Promise<PostTemplate | null> {
      if (this.saving) return null
      this.saving = true
      try {
        const created = await templatesApi.create(name, body)
        this.list.unshift(created)
        this.setHighlight(created.id)
        return created
      } catch (e) {
        useToastStore().error(messageOf(e, 'Не удалось создать шаблон'))
        return null
      } finally {
        this.saving = false
      }
    },

    async update(id: string, patch: { name?: string; body?: string }): Promise<PostTemplate | null> {
      if (this.saving) return null
      this.saving = true
      try {
        const fresh = await templatesApi.update(id, patch)
        const at = this.list.findIndex((t) => t.id === id)
        if (at !== -1) this.list.splice(at, 1, fresh)
        return fresh
      } catch (e) {
        useToastStore().error(messageOf(e, 'Не удалось сохранить шаблон'))
        return null
      } finally {
        this.saving = false
      }
    },

    async remove(id: string): Promise<boolean> {
      try {
        await templatesApi.remove(id)
        const at = this.list.findIndex((t) => t.id === id)
        if (at !== -1) this.list.splice(at, 1)
        if (this.highlightId === id) this.clearHighlight()
        return true
      } catch (e) {
        useToastStore().error(messageOf(e, 'Не удалось удалить шаблон'))
        return false
      }
    },

    async makeDefault(id: string): Promise<boolean> {
      const current = this.list.find((t) => t.id === id)
      if (!current || current.is_default) return false
      try {
        const fresh = await templatesApi.makeDefault(id)
        for (const t of this.list) t.is_default = t.id === fresh.id
        const at = this.list.findIndex((t) => t.id === id)
        if (at !== -1) this.list.splice(at, 1, fresh)
        return true
      } catch (e) {
        useToastStore().error(messageOf(e, 'Не удалось сделать активным'))
        return false
      }
    },

    /**
     * Рендер шаблона на демо-данных. Ошибку НЕ шлём в тост (превью дёргается
     * на каждый ввод) — вызывающий показывает её текстом под полем.
     */
    async preview(body: string): Promise<string> {
      const { caption } = await templatesApi.preview(body)
      return caption
    },

    /**
     * Собирает шаблон по описанию и сразу сохраняет — как и клонирование
     * дизайна. Пользователь дальше правит его в редакторе.
     */
    async generateFromBrief(brief: string): Promise<PostTemplate | null> {
      if (this.generating) return null
      this.generating = true
      try {
        const draft = await templatesApi.generate(brief)
        return await this.create(draft.name, draft.body)
      } catch (e) {
        useToastStore().error(messageOf(e, 'Не удалось собрать шаблон'))
        return null
      } finally {
        this.generating = false
      }
    },

    // ------------------- «Скопировать дизайн из поста» ------------------- //

    /** Создаёт сессию капчи и запускает поллинг. Возвращает deep-link. */
    async startCapture(): Promise<string | null> {
      if (this.captureStarting) return null
      this.cancelCapture()
      this.captureStarting = true
      try {
        const session = await templatesApi.startCapture()
        this.captureToken = session.token
        this.captureDeepLink = session.deep_link
        this.captureStatus = 'waiting'
        this.captureError = null
        const ttl = session.expires_in > 0 ? session.expires_in * 1000 : POLL_TIMEOUT_MS
        this.pollCapture(Math.min(ttl, POLL_TIMEOUT_MS))
        return session.deep_link
      } catch (e) {
        this.captureStatus = null
        useToastStore().error(messageOf(e, 'Не удалось начать копирование'))
        return null
      } finally {
        this.captureStarting = false
      }
    },

    /** Запускает опрос статуса. Повторный вызов перезапускает интервал. */
    pollCapture(ttlMs: number = POLL_TIMEOUT_MS): void {
      this.stopPolling()
      if (!this.captureToken) return
      pollDeadline = Date.now() + ttlMs
      pollTimer = window.setInterval(() => {
        void this.tickCapture()
      }, POLL_MS)
    },

    /** Один шаг опроса. Отдельный экшен, чтобы интервал не тащил замыкания. */
    async tickCapture(): Promise<void> {
      const token = this.captureToken
      if (!token) {
        this.stopPolling()
        return
      }
      if (pollInflight) return
      if (Date.now() > pollDeadline) {
        this.finishCapture('expired', 'Время ожидания истекло — попробуйте ещё раз')
        return
      }
      pollInflight = true
      try {
        const st = await templatesApi.captureStatus(token)
        // Пока крутился запрос, пользователь мог отменить ожидание.
        if (this.captureToken !== token) return
        // waiting — ссылка выдана, armed — бот принял /start и ждёт пример.
        if (st.status === 'waiting' || st.status === 'armed') {
          this.captureStatus = st.status
          return
        }
        if (st.status === 'done') {
          this.stopPolling()
          this.captureToken = null
          this.captureStatus = 'done'
          this.captureError = null
          await this.fetch()
          if (st.template_id) this.setHighlight(st.template_id)
          useToastStore().success('✨ Шаблон создан из вашего поста')
          return
        }
        this.finishCapture(
          st.status,
          st.error ??
            (st.status === 'expired' ? 'Время ожидания истекло' : 'Не удалось разобрать пост'),
        )
      } catch (e) {
        if (this.captureToken !== token) return
        this.finishCapture('error', messageOf(e, 'Не удалось получить статус'))
      } finally {
        pollInflight = false
      }
    },

    /** Завершение сессии неуспехом: гасим таймер, сообщаем пользователю. */
    finishCapture(status: CaptureState, message: string): void {
      this.stopPolling()
      this.captureToken = null
      this.captureStatus = status
      this.captureError = message
      useToastStore().error(message)
    },

    /** Останавливает только таймер опроса. */
    stopPolling(): void {
      if (pollTimer !== null) {
        clearInterval(pollTimer)
        pollTimer = null
      }
      pollInflight = false
    },

    /** Полный сброс сессии капчи (отмена пользователем / уход с экрана). */
    cancelCapture(): void {
      const token = this.captureToken
      this.stopPolling()
      this.captureToken = null
      this.captureDeepLink = null
      this.captureStatus = null
      this.captureError = null
      // Гасим сессию и на сервере, иначе бот будет ждать пример ещё 15 минут
      // и перехватит следующее обычное сообщение пользователя.
      if (token) void templatesApi.cancelCapture(token).catch(() => undefined)
    },

    setHighlight(id: string): void {
      this.clearHighlight()
      this.highlightId = id
      highlightTimer = window.setTimeout(() => {
        this.highlightId = null
        highlightTimer = null
      }, HIGHLIGHT_MS)
    },

    clearHighlight(): void {
      if (highlightTimer !== null) {
        clearTimeout(highlightTimer)
        highlightTimer = null
      }
      this.highlightId = null
    },
  },
})

function messageOf(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.message || fallback
  return e instanceof Error ? e.message : fallback
}
