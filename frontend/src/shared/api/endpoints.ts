/** Типизированные вызовы эндпоинтов. Пути — строго по контракту. */
import { http, uploadFile } from './http'
import type {
  ActivityPage,
  AdminScope,
  AnalyticsSummary,
  Currency,
  InviteOut,
  ItemCreate,
  ItemFilters,
  ItemOut,
  ItemPage,
  ItemUpdate,
  MemberOut,
  OversightOut,
  Role,
  StatusPatch,
  StoreOut,
  SwitchStoreResult,
  TeamOverview,
} from './types'

const V1 = '/api/v1'

// ------------------------------- Stores ------------------------------- //
export const storesApi = {
  list: () => http.get<StoreOut[]>(`${V1}/stores`),
  switch: (storeId: string) =>
    http.post<SwitchStoreResult>(`${V1}/stores/switch`, { store_id: storeId }),
  members: () => http.get<MemberOut[]>(`${V1}/stores/members`),
  invite: (username: string, role: Exclude<Role, 'OWNER'>) =>
    http.post<InviteOut>(`${V1}/stores/invites`, { username, role }),
  revokeInvite: (inviteId: string) => http.del<void>(`${V1}/stores/invites/${inviteId}`),
  getSettings: () => http.get<{ base_currency: Currency }>(`${V1}/stores/settings`),
  setBaseCurrency: (base: Currency) =>
    http.patch<{ base_currency: Currency }>(`${V1}/stores/settings`, { base_currency: base }),
  fx: () => http.get<{ base: Currency; rates: Record<string, number> }>(`${V1}/stores/fx`),
}

// ------------------------------- Items ------------------------------- //
export interface ListItemsParams extends ItemFilters {
  cursor?: string | null
  limit?: number
  archived?: boolean
}

export const itemsApi = {
  list: (params: ListItemsParams, signal?: AbortSignal) =>
    http.get<ItemPage>(
      `${V1}/items`,
      {
        cursor: params.cursor ?? undefined,
        limit: params.limit ?? undefined,
        status: params.status ?? undefined,
        brand: params.brand ?? undefined,
        search: params.search ?? undefined,
        ids: params.ids && params.ids.length ? params.ids.join(',') : undefined,
        archived: params.archived ? true : undefined,
      },
      signal,
    ),

  suggest: (field: 'brand' | 'category', q: string, signal?: AbortSignal) =>
    http.get<string[]>(`${V1}/items/suggest/${field}`, { q }, signal),

  get: (id: string) => http.get<ItemOut>(`${V1}/items/${id}`),

  create: (payload: ItemCreate) => http.post<ItemOut>(`${V1}/items`, payload),

  patchStatus: (id: string, payload: StatusPatch, idempotencyKey: string) =>
    http.patch<ItemOut>(`${V1}/items/${id}/status`, payload, {
      'Idempotency-Key': idempotencyKey,
    }),

  /** Мягкое удаление (в архив). */
  archive: (id: string) => http.del<void>(`${V1}/items/${id}`),

  /** Безвозвратное удаление. */
  remove: (id: string) => http.del<void>(`${V1}/items/${id}?hard=true`),

  /** Достать из архива. */
  restore: (id: string) => http.post<ItemOut>(`${V1}/items/${id}/restore`),

  /** Редактирование полей товара. */
  update: (id: string, patch: ItemUpdate) => http.patch<ItemOut>(`${V1}/items/${id}`, patch),

}

// ------------------------------- Media ------------------------------- //
export const mediaApi = {
  /** Загрузка фото из галереи/камеры → { photo_id: "local:<name>" }. */
  upload: (file: File, signal?: AbortSignal) =>
    uploadFile<{ photo_id: string }>(`${V1}/media/upload`, file, signal),
}

// ------------------------------- Analytics ------------------------------- //
export const analyticsApi = {
  summary: () => http.get<AnalyticsSummary>(`${V1}/analytics/summary`),
}

/** Путь медиа для AuthImage (грузится через fetchBlob c заголовком initData). */
export function mediaPath(itemId: string, index: number, width?: number): string {
  const base = `${V1}/media/${itemId}/${index}`
  return width ? `${base}?w=${width}` : base
}

// ------------------------------- Админка ------------------------------- //
/** Доступно только владельцу склада; остальным сервер отвечает 403. */
export const adminApi = {
  activity: (p: {
    storeId?: string | null
    userId?: string | null
    group?: string | null
    days?: number
    limit?: number
    offset?: number
  }) =>
    http.get<ActivityPage>(`${V1}/admin/activity`, {
      store_id: p.storeId ?? undefined,
      user_id: p.userId ?? undefined,
      group: p.group ?? undefined,
      days: p.days ?? undefined,
      limit: p.limit ?? undefined,
      offset: p.offset || undefined,
    }),
  team: (days = 30, storeId?: string | null) =>
    http.get<TeamOverview>(`${V1}/admin/team`, {
      days,
      store_id: storeId ?? undefined,
    }),
  /** Склады, доступные в панели: свои + открытые по надзору. */
  scopes: () => http.get<AdminScope[]>(`${V1}/admin/scopes`),
  oversight: () => http.get<OversightOut[]>(`${V1}/admin/oversight`),
  requestOversight: (username: string) =>
    http.post<OversightOut>(`${V1}/admin/oversight`, { username }),
  dropOversight: (id: string) => http.del<void>(`${V1}/admin/oversight/${id}`),
}
