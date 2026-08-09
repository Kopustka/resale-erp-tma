/** Типы контракта API. Совпадают байт-в-байт с бэкендом (Pydantic-схемы). */

export type ItemStatus =
  | 'BOUGHT'
  | 'PREPARING'
  | 'PHOTOGRAPHED'
  | 'LISTED'
  | 'BOOKED'
  | 'SOLD'
  | 'SHIPPED'
  | 'COMPLETED'
  | 'CANCELLED'
  | 'RETURNED'

export type Role = 'OWNER' | 'EMPLOYEE' | 'ANALYST'

export type Currency = 'BYN' | 'RUB' | 'USD' | 'EUR'
export const CURRENCIES: Currency[] = ['BYN', 'RUB', 'USD', 'EUR']
export const CURRENCY_SYMBOLS: Record<Currency, string> = {
  BYN: 'Br',
  RUB: '₽',
  USD: '$',
  EUR: '€',
}

export interface ItemOut {
  id: string
  sku: string
  title: string
  brand: string
  category: string
  size: string | null
  color: string | null
  condition: string | null
  length_cm: number | null
  width_cm: number | null
  sleeve_cm: number | null
  description: string | null
  photo_count: number
  status: ItemStatus
  version: number
  listed_date: string | null
  sold_date: string | null
  created_at: string
  // финансы. cost_price/…/list_price — ОРИГИНАЛ в валюте ввода.
  cost_price?: number | null
  restore_cost?: number | null
  delivery_cost?: number | null
  platform_fee?: number | null
  selling_price?: number | null
  list_price?: number | null
  cost_currency: Currency
  price_currency: Currency
  // значения в базовой валюте склада (для карточек и прибыли)
  cost_price_base?: number | null
  selling_price_base?: number | null
  list_price_base?: number | null
  net_profit?: number | null
  roi_percent?: number | null
  purchase_location?: string | null
  sales_platform?: string | null
}

export interface ItemCreate {
  title: string
  brand: string
  category: string
  size?: string
  color?: string
  condition?: string
  description?: string
  cost_price?: number
  restore_cost?: number
  delivery_cost?: number
  list_price?: number | null
  cost_currency?: Currency
  price_currency?: Currency
  photo_file_ids?: string[]
  purchase_location?: string
  sales_platform?: string
  ad_url?: string
}

/** Частичное обновление товара (редактирование). */
export interface ItemUpdate {
  title?: string
  brand?: string
  category?: string
  size?: string | null
  color?: string | null
  condition?: string | null
  length_cm?: number | null
  width_cm?: number | null
  sleeve_cm?: number | null
  description?: string | null
  cost_price?: number
  restore_cost?: number
  delivery_cost?: number
  platform_fee?: number
  selling_price?: number | null
  list_price?: number | null
  cost_currency?: Currency
  price_currency?: Currency
  sales_platform?: string | null
  ad_url?: string | null
}

export interface ItemPage {
  items: ItemOut[]
  next_cursor: string | null
}

export interface StatusPatch {
  target_status?: ItemStatus
  version: number
  selling_price?: number
  selling_currency?: Currency
}

/** Сгенерированные AI название и описание (подставляются в форму, не сохраняются). */
export interface AiDescribeResult {
  title: string
  description: string
}

/** Результат разбора голосовой фразы для предзаполнения формы создания. */
export interface VoiceParseResult {
  brand: string | null
  category: string | null
  size: string | null
  color: string | null
  condition: string | null
  cost_price: number | null
  title: string | null
  low_confidence: boolean
}

export interface StaleBucket {
  threshold_days: number
  count: number
  item_ids: string[]
}

export interface LocationRoi {
  location: string
  invested: number
  profit: number
  roi_percent: number | null
}

export interface TurnoverPoint {
  period: string
  category: string
  avg_days: number
  sold_count: number
}

export interface AnalyticsSummary {
  stale: StaleBucket
  by_location: LocationRoi[]
  turnover: TurnoverPoint[]
  total_profit: number
  active_count: number
}

export interface StoreOut {
  id: string
  name: string
  role: Role
  base_currency: Currency
}

export interface MemberOut {
  user_id: string
  username: string | null
  first_name: string | null
  role: Role
}

export interface InviteOut {
  id: string
  username: string
  role: Role
  status: string
}

export interface SwitchStoreResult {
  ok: boolean
  current_store_id: string
}

/** Настройки автопостинга склада. */
export interface ChannelSettings {
  channel_id: string | null
  channel_signature: string | null
  watermark_enabled: boolean
  watermark_text: string | null
}

/** Частичное обновление настроек автопостинга. */
export interface ChannelUpdate {
  channel_id?: string | null
  channel_signature?: string | null
  watermark_enabled?: boolean | null
  watermark_text?: string | null
}

// --------------------------- Шаблоны постов --------------------------- //

/** Шаблон подписи для автопостинга в канал. */
export interface PostTemplate {
  id: string
  name: string
  body: string
  is_default: boolean
  created_at: string
  updated_at: string
}

/** Доступный плейсхолдер шаблона. key — без фигурных скобок (напр. "title"). */
export interface TemplatePlaceholder {
  key: string
  label: string
  example: string
}

/** Сессия «скопировать дизайн из поста»: ссылка в чат с ботом + токен для поллинга. */
export interface CaptureSession {
  token: string
  deep_link: string
  expires_in: number
}

/**
 * waiting — сессия создана, юзер ещё не открыл чат с ботом;
 * armed   — бот принял deep link и ждёт пример поста.
 */
export type CaptureState = 'waiting' | 'armed' | 'done' | 'expired' | 'error'

export interface CaptureStatus {
  status: CaptureState
  template_id: string | null
  error: string | null
}

/** Поля фильтров списка склада. */
export interface ItemFilters {
  status?: ItemStatus | null
  brand?: string | null
  category?: string | null
  search?: string | null
  ids?: string[] | null
}
