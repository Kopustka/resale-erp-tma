import { defineStore } from 'pinia'
import { storesApi } from '@/shared/api/endpoints'
import { ApiError } from '@/shared/api/http'
import type { Currency, InviteOut, MemberOut, Role, StoreOut } from '@/shared/api/types'
import { setBaseCurrency } from '@/shared/utils/format'

const LS_STORE_KEY = 'resale.currentStoreId'

interface SessionState {
  stores: StoreOut[]
  currentStoreId: string | null
  members: MemberOut[]
  /**
   * Локально отслеживаемые приглашения (GET-эндпоинта списка инвайтов в контракте нет,
   * поэтому pending-инвайты, созданные в этой сессии, храним здесь для возможности отзыва).
   */
  pendingInvites: InviteOut[]
  loading: boolean
  membersLoading: boolean
  ready: boolean
  error: string | null
}

export const useSessionStore = defineStore('session', {
  state: (): SessionState => ({
    stores: [],
    currentStoreId: null,
    members: [],
    pendingInvites: [],
    loading: false,
    membersLoading: false,
    ready: false,
    error: null,
  }),

  getters: {
    currentStore(state): StoreOut | null {
      return state.stores.find((s) => s.id === state.currentStoreId) ?? null
    },
    role(): Role | null {
      return this.currentStore?.role ?? null
    },
    baseCurrency(): Currency {
      return this.currentStore?.base_currency ?? 'BYN'
    },
    isOwner(): boolean {
      return this.role === 'OWNER'
    },
    /** Финансы и BI видны OWNER и ANALYST. */
    canSeeFinance(): boolean {
      return this.role === 'OWNER' || this.role === 'ANALYST'
    },
    /** Операционка (создание/правка/статусы) — OWNER и EMPLOYEE. */
    canEdit(): boolean {
      return this.role === 'OWNER' || this.role === 'EMPLOYEE'
    },
  },

  actions: {
    /**
     * Инициализация: тянем склады, выбираем текущий (из localStorage либо первый),
     * синхронизируем серверный current_store через switch, чтобы список/роль совпали.
     */
    async init(): Promise<void> {
      this.loading = true
      this.error = null
      try {
        this.stores = await storesApi.list()
        if (this.stores.length === 0) {
          this.currentStoreId = null
          this.ready = true
          return
        }
        const saved = localStorage.getItem(LS_STORE_KEY)
        const chosen =
          (saved && this.stores.find((s) => s.id === saved)?.id) || this.stores[0].id
        await this.selectStore(chosen)
        this.ready = true
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Не удалось загрузить склады'
      } finally {
        this.loading = false
      }
    },

    async selectStore(storeId: string): Promise<void> {
      const res = await storesApi.switch(storeId)
      this.currentStoreId = res.current_store_id
      localStorage.setItem(LS_STORE_KEY, res.current_store_id)
      setBaseCurrency(this.baseCurrency)
    },

    /** Обновить базовую валюту после смены в настройках. */
    applyBaseCurrency(cur: Currency): void {
      const s = this.stores.find((x) => x.id === this.currentStoreId)
      if (s) s.base_currency = cur
      setBaseCurrency(cur)
    },

    async fetchMembers(): Promise<void> {
      if (!this.isOwner) return
      this.membersLoading = true
      try {
        this.members = await storesApi.members()
      } finally {
        this.membersLoading = false
      }
    },

    async createInvite(username: string, role: Exclude<Role, 'OWNER'>): Promise<InviteOut> {
      const clean = username.replace(/^@/, '').trim()
      const invite = await storesApi.invite(clean, role)
      if (invite.status === 'PENDING') {
        this.pendingInvites.unshift(invite)
      } else {
        // мгновенно принят — обновим список участников
        await this.fetchMembers()
      }
      return invite
    },

    async revokeInvite(inviteId: string): Promise<void> {
      await storesApi.revokeInvite(inviteId)
      this.pendingInvites = this.pendingInvites.filter((i) => i.id !== inviteId)
    },
  },
})

export { ApiError }
