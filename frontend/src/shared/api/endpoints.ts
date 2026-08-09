/** Типизированные вызовы эндпоинтов. Пути — строго по контракту. */
import { http, uploadFile } from './http'
import type {
  AiDescribeResult,
  AnalyticsSummary,
  Currency,
  InviteOut,
  ItemCreate,
  ItemFilters,
  ItemOut,
  ItemPage,
  ItemUpdate,
  MemberOut,
  Role,
  StatusPatch,
  StoreOut,
  SwitchStoreResult,
  VoiceParseResult,
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
  getChannel: () =>
    http.get<{ channel_id: string | null; channel_signature: string | null }>(
      `${V1}/stores/channel`,
    ),
  setChannel: (channelId: string | null, signature: string | null) =>
    http.patch<{ channel_id: string | null; channel_signature: string | null }>(
      `${V1}/stores/channel`,
      { channel_id: channelId, channel_signature: signature },
    ),
  testChannel: () => http.post<{ ok: boolean }>(`${V1}/stores/channel/test`),
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

  /** Разбор голосовой фразы в поля новой вещи. */
  parseVoice: (text: string) =>
    http.post<VoiceParseResult>(`${V1}/items/parse-voice`, { text }),

  /** AI-перегенерация названия/описания по фото вещи (без сохранения). */
  aiDescribe: (id: string) => http.post<AiDescribeResult>(`${V1}/items/${id}/ai-describe`),
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
export function mediaPath(itemId: string, index: number): string {
  return `${V1}/media/${itemId}/${index}`
}
