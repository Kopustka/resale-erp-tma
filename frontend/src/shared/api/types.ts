/** Типы контракта API. Совпадают байт-в-байт с бэкендом (Pydantic-схемы). */

export type ItemStatus =
  | 'BOUGHT'
  | 'PREPARING'
  | 'PHOTOGRAPHED'
  | 'LISTED'
  | 'SHIPPED'

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
  /** Цена до скидки — для зачёркнутого ценника. */
  price_before_discount?: number | null
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
  /** Значения полей, заведённых магазином (см. FormField). */
  extra?: Record<string, string>
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
  /** Значения своих полей магазина. */
  extra?: Record<string, string>
  /** Версия карточки на момент открытия формы — защита от затирания. */
  version?: number
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

export interface StageStat {
  status: ItemStatus
  avg_days: number
  max_days: number
  /** Сколько раз проходили этап, с учётом откатов назад. */
  passes: number
  /** Сколько вещей стоит на нём прямо сейчас. */
  now_here: number
}

export interface GroupStat {
  name: string
  total: number
  sold: number
  profit: number
  /** Наценка к вложенному, %. null — ещё нечего считать. */
  markup: number | null
  avg_days: number | null
  /** Вложено в непроданное по этой группе. */
  frozen: number
}

export interface MonthPoint {
  month: string
  sold: number
  revenue: number
  profit: number
}

export interface AnalyticsSummary {
  stale: StaleBucket
  by_location: LocationRoi[]
  turnover: TurnoverPoint[]
  total_profit: number
  active_count: number
  stages: StageStat[]
  by_brand: GroupStat[]
  by_category: GroupStat[]
  by_month: MonthPoint[]
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
  bump_enabled: boolean
  bump_after_days: number
  preview_before_post: boolean
  subscriptions_enabled: boolean
  auto_reply_enabled: boolean
  discount_template: string | null
}

/** Частичное обновление настроек автопостинга. */
export interface ChannelUpdate {
  channel_id?: string | null
  channel_signature?: string | null
  watermark_enabled?: boolean | null
  watermark_text?: string | null
  bump_enabled?: boolean | null
  bump_after_days?: number | null
  preview_before_post?: boolean | null
  subscriptions_enabled?: boolean | null
  auto_reply_enabled?: boolean | null
  discount_template?: string | null
}

/** Канал автопостинга склада. */
export interface Channel {
  id: string
  chat_id: string
  title: string | null
  signature: string | null
  enabled: boolean
  posts_count: number
}

export interface ChannelCreate {
  chat_id: string
  title?: string | null
  signature?: string | null
}

export interface ChannelPatch {
  title?: string | null
  signature?: string | null
  enabled?: boolean | null
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

/** Результат создания подборки. */
export interface DropOut {
  id: string
  title: string | null
  item_count: number
  with_photo: number
  channels: number
}

/** Свободный пост в расписании. */
export interface CustomPost {
  id: string
  body: string
  photo_count: number
  scheduled_at: string | null
  status: 'SCHEDULED' | 'PUBLISHED' | 'CANCELLED'
  created_at: string
}

/** Поля фильтров списка склада. */
export interface ItemFilters {
  status?: ItemStatus | null
  brand?: string | null
  category?: string | null
  search?: string | null
  ids?: string[] | null
}

// -------------------------------- Админка -------------------------------- //

export type ActivityGroup = 'items' | 'status' | 'publishing' | 'settings'

export interface ActivityActor {
  user_id: string | null
  username: string | null
  first_name: string | null
  role: Role | null
}

export interface ActivityEvent {
  id: string
  at: string
  actor: ActivityActor
  action: string
  group: ActivityGroup
  icon: string
  title: string
  summary: string
  entity_type: string | null
  entity_id: string | null
}

export interface ActivityPage {
  events: ActivityEvent[]
  has_more: boolean
}

export interface MemberStats {
  user_id: string
  username: string | null
  first_name: string | null
  role: Role
  joined_at: string | null
  operations: number
  items_added: number
  listed: number
  shipped: number
  discounts: number
  last_action_at: string | null
}

export interface PendingInvite {
  id: string
  username: string
  role: Role
  status: string
  created_at: string
}

export interface TeamOverview {
  days: number
  members: MemberStats[]
  invites: PendingInvite[]
}

export interface AdminScope {
  store_id: string
  name: string
  kind: 'own' | 'watch'
  owner_name: string | null
}

export interface OversightOut {
  id: string
  target_username: string
  store_id: string | null
  store_name: string | null
  status: string
  created_at: string
}

// --------------------------- Настраиваемая форма --------------------------- //

export type FieldKind = 'TEXT' | 'TEXTAREA' | 'NUMBER' | 'MONEY' | 'SELECT'

export interface FormField {
  id: string
  key: string
  label: string
  kind: FieldKind
  enabled: boolean
  required: boolean
  position: number
  builtin: boolean
  options: string[]
  hint: string | null
  /** Поле нельзя скрыть или сделать необязательным — решает сервер. */
  locked: boolean
}

export interface FieldPatch {
  id: string
  label?: string
  enabled?: boolean
  required?: boolean
  position?: number
  hint?: string | null
  options?: string[]
}

export interface FieldCreate {
  label: string
  kind: FieldKind
  required?: boolean
  hint?: string | null
  options?: string[]
}
