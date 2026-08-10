/** Типизированные вызовы эндпоинтов. Пути — строго по контракту. */
import { http, uploadFile } from './http'
import type {
  AiDescribeResult,
  AnalyticsSummary,
  CaptureSession,
  CaptureStatus,
  Channel,
  ChannelCreate,
  ChannelPatch,
  ChannelSettings,
  DropOut,
  ChannelUpdate,
  Currency,
  InviteOut,
  ItemCreate,
  ItemFilters,
  ItemOut,
  ItemPage,
  ItemUpdate,
  MemberOut,
  PostTemplate,
  Role,
  StatusPatch,
  StoreOut,
  SwitchStoreResult,
  TemplatePlaceholder,
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
  getChannel: () => http.get<ChannelSettings>(`${V1}/stores/channel`),
  setChannel: (patch: ChannelUpdate) =>
    http.patch<ChannelSettings>(`${V1}/stores/channel`, patch),
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

// ------------------------------ Каналы ------------------------------ //
export const channelsApi = {
  list: () => http.get<Channel[]>(`${V1}/channels`),
  create: (payload: ChannelCreate) => http.post<Channel>(`${V1}/channels`, payload),
  update: (id: string, patch: ChannelPatch) =>
    http.patch<Channel>(`${V1}/channels/${id}`, patch),
  remove: (id: string) => http.del<void>(`${V1}/channels/${id}`),
  test: (id: string) => http.post<{ ok: boolean }>(`${V1}/channels/${id}/test`),
}

// ---------------------------- Шаблоны постов ---------------------------- //
export const templatesApi = {
  list: () => http.get<PostTemplate[]>(`${V1}/templates`),

  create: (name: string, body: string) =>
    http.post<PostTemplate>(`${V1}/templates`, { name, body }),

  update: (id: string, patch: { name?: string; body?: string }) =>
    http.patch<PostTemplate>(`${V1}/templates/${id}`, patch),

  remove: (id: string) => http.del<void>(`${V1}/templates/${id}`),

  /** Сделать шаблон активным (снимает флаг с остальных). */
  makeDefault: (id: string) => http.post<PostTemplate>(`${V1}/templates/${id}/default`),

  placeholders: () => http.get<TemplatePlaceholder[]>(`${V1}/templates/placeholders`),

  /** Рендер тела шаблона на демо-данных → HTML-подпись поста. */
  preview: (body: string) => http.post<{ caption: string }>(`${V1}/templates/preview`, { body }),

  /** Собрать шаблон по словесному описанию (не сохраняет). */
  generate: (brief: string) =>
    http.post<{ name: string; body: string }>(`${V1}/templates/generate`, { brief }),

  /** Старт сессии «скопировать дизайн из поста». */
  startCapture: () => http.post<CaptureSession>(`${V1}/templates/capture`),

  captureStatus: (token: string) => http.get<CaptureStatus>(`${V1}/templates/capture/${token}`),

  /** Снять сессию на сервере, чтобы бот перестал ждать пример поста. */
  cancelCapture: (token: string) => http.del<void>(`${V1}/templates/capture/${token}`),
}

// ------------------------------- Дропы ------------------------------- //
export const dropsApi = {
  /** Опубликовать выбранные вещи одним альбомом. */
  create: (itemIds: string[], title?: string, note?: string) =>
    http.post<DropOut>(`${V1}/drops`, { item_ids: itemIds, title, note }),
}

// ------------------------------- Analytics ------------------------------- //
export const analyticsApi = {
  summary: () => http.get<AnalyticsSummary>(`${V1}/analytics/summary`),
}

/** Путь медиа для AuthImage (грузится через fetchBlob c заголовком initData). */
export function mediaPath(itemId: string, index: number): string {
  return `${V1}/media/${itemId}/${index}`
}
