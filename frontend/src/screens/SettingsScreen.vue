<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { openTemplates } from '@/app/navigation'
import { useSessionStore } from '@/stores/session'
import { useItemsStore } from '@/stores/items'
import { useAnalyticsStore } from '@/stores/analytics'
import { useTemplatesStore } from '@/stores/templates'
import { useToastStore } from '@/stores/toast'
import { CURRENCIES, CURRENCY_SYMBOLS, type Currency, type Role } from '@/shared/api/types'
import { storesApi } from '@/shared/api/endpoints'
import { hapticSelection } from '@/shared/telegram/webapp'

const session = useSessionStore()
const items = useItemsStore()
const analytics = useAnalyticsStore()
const templates = useTemplatesStore()
const toast = useToastStore()

/** Подпись под строкой перехода: какой шаблон сейчас используется. */
const activeTemplateName = computed(() => {
  if (templates.loading && !templates.list.length) return 'Загрузка…'
  return templates.activeTemplate?.name ?? 'Стандартное оформление'
})

function goTemplates(): void {
  hapticSelection()
  openTemplates()
}

const ROLE_LABELS: Record<Role, string> = {
  OWNER: 'Владелец',
  EMPLOYEE: 'Сотрудник',
  ANALYST: 'Аналитик',
}

const inviteUsername = ref('')
const inviteRole = ref<Exclude<Role, 'OWNER'>>('EMPLOYEE')
const inviting = ref(false)
const switching = ref(false)

// --- Основная валюта ---
const baseCurrency = ref<Currency>('BYN')
const currencyBusy = ref(false)

async function changeBaseCurrency(cur: Currency): Promise<void> {
  if (cur === baseCurrency.value || currencyBusy.value) return
  currencyBusy.value = true
  try {
    const { base_currency } = await storesApi.setBaseCurrency(cur)
    baseCurrency.value = base_currency
    session.applyBaseCurrency(base_currency)
    await items.loadFirst()
    toast.success(`Основная валюта: ${base_currency}`)
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось сменить валюту')
  } finally {
    currencyBusy.value = false
  }
}

// --- Автопостинг в канал ---
const channelInput = ref('')
const channelSignature = ref('')
const channelSaved = ref<string | null>(null)
const channelBusy = ref(false)
const watermarkEnabled = ref(false)
const watermarkText = ref('')

async function loadChannel(): Promise<void> {
  try {
    const ch = await storesApi.getChannel()
    channelSaved.value = ch.channel_id
    channelInput.value = ch.channel_id ?? ''
    channelSignature.value = ch.channel_signature ?? ''
    watermarkEnabled.value = ch.watermark_enabled
    watermarkText.value = ch.watermark_text ?? ''
  } catch {
    /* игнор — просто пусто */
  }
}

async function saveChannel(): Promise<void> {
  channelBusy.value = true
  try {
    const { channel_id } = await storesApi.setChannel({
      channel_id: channelInput.value.trim() || null,
      channel_signature: channelSignature.value.trim() || null,
      watermark_enabled: watermarkEnabled.value,
      watermark_text: watermarkText.value.trim() || null,
    })
    channelSaved.value = channel_id
    channelInput.value = channel_id ?? ''
    toast.success(channel_id ? 'Канал сохранён' : 'Автопостинг выключен')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось сохранить')
  } finally {
    channelBusy.value = false
  }
}

async function testChannel(): Promise<void> {
  channelBusy.value = true
  try {
    await storesApi.testChannel()
    toast.success('Тестовое сообщение отправлено в канал ✅')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось отправить')
  } finally {
    channelBusy.value = false
  }
}

onMounted(() => {
  baseCurrency.value = session.baseCurrency
  if (session.isOwner) {
    void session.fetchMembers()
    void loadChannel()
    void templates.fetch()
    void storesApi.getSettings().then((s) => (baseCurrency.value = s.base_currency))
  }
})

async function switchStore(id: string): Promise<void> {
  if (id === session.currentStoreId || switching.value) return
  switching.value = true
  hapticSelection()
  try {
    await session.selectStore(id)
    // Перезагружаем зависимые данные под новую роль/склад.
    await items.loadFirst()
    analytics.summary = null
    session.members = []
    if (session.isOwner) await session.fetchMembers()
    toast.success('Склад переключён')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось переключить склад')
  } finally {
    switching.value = false
  }
}

async function sendInvite(): Promise<void> {
  const uname = inviteUsername.value.trim()
  if (!uname || inviting.value) return
  inviting.value = true
  try {
    const invite = await session.createInvite(uname, inviteRole.value)
    inviteUsername.value = ''
    toast.success(
      invite.status === 'PENDING'
        ? `Приглашение отправлено @${invite.username}`
        : `@${invite.username} добавлен`,
    )
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось пригласить')
  } finally {
    inviting.value = false
  }
}

async function revoke(id: string): Promise<void> {
  try {
    await session.revokeInvite(id)
    toast.success('Приглашение отозвано')
  } catch (e) {
    toast.error(e instanceof Error ? e.message : 'Не удалось отозвать')
  }
}

function exportCsv(): void {
  // TODO: реального эндпоинта экспорта пока нет. Заглушка отправки в бота.
  toast.success('Файл отправлен в бота (заглушка)')
}
</script>

<template>
  <div class="settings">
    <header class="head">
      <h1 class="title">Настройки</h1>
    </header>

    <div class="content no-scrollbar">
      <!-- Текущая роль -->
      <section class="block">
        <div class="role-line">
          <span class="hint">Ваша роль</span>
          <span class="role-pill">{{ session.role ? ROLE_LABELS[session.role] : '—' }}</span>
        </div>
      </section>

      <!-- Склады -->
      <section class="block">
        <h2 class="block-title">Активный склад</h2>
        <div v-if="session.stores.length" class="store-list">
          <button
            v-for="s in session.stores"
            :key="s.id"
            class="store-row"
            :class="{ active: s.id === session.currentStoreId }"
            :disabled="switching"
            @click="switchStore(s.id)"
          >
            <div class="store-info">
              <span class="store-name">{{ s.name }}</span>
              <span class="store-role hint">{{ ROLE_LABELS[s.role] }}</span>
            </div>
            <span v-if="s.id === session.currentStoreId" class="check">
              <svg viewBox="0 0 24 24" width="20" height="20">
                <path fill="currentColor" d="m9 16.2-3.5-3.5-1.4 1.4L9 19 20 8l-1.4-1.4z" />
              </svg>
            </span>
          </button>
        </div>
        <p v-else class="hint">Нет доступных складов.</p>
      </section>

      <!-- Команда (только OWNER) -->
      <section v-if="session.isOwner" class="block">
        <h2 class="block-title">Команда</h2>

        <div v-if="session.membersLoading" class="hint">Загрузка участников…</div>
        <div v-else class="members">
          <div v-for="m in session.members" :key="m.user_id" class="member">
            <div class="m-info">
              <span class="m-name">{{ m.first_name || m.username || '—' }}</span>
              <span v-if="m.username" class="m-uname hint">@{{ m.username }}</span>
            </div>
            <span class="m-role">{{ ROLE_LABELS[m.role] }}</span>
          </div>
        </div>

        <!-- Pending инвайты этой сессии -->
        <div v-if="session.pendingInvites.length" class="pending">
          <div v-for="inv in session.pendingInvites" :key="inv.id" class="member pending-row">
            <div class="m-info">
              <span class="m-name">@{{ inv.username }}</span>
              <span class="m-uname hint">ожидает · {{ ROLE_LABELS[inv.role] }}</span>
            </div>
            <button class="revoke" @click="revoke(inv.id)">Отозвать</button>
          </div>
        </div>

        <!-- Форма приглашения -->
        <div class="invite">
          <label class="lbl">Пригласить по @username</label>
          <div class="invite-row">
            <input v-model="inviteUsername" class="field" placeholder="@username" autocomplete="off" />
          </div>
          <div class="role-select">
            <button
              class="role-opt"
              :class="{ sel: inviteRole === 'EMPLOYEE' }"
              @click="inviteRole = 'EMPLOYEE'"
            >
              Сотрудник
            </button>
            <button
              class="role-opt"
              :class="{ sel: inviteRole === 'ANALYST' }"
              @click="inviteRole = 'ANALYST'"
            >
              Аналитик
            </button>
          </div>
          <button class="btn-primary tap" :disabled="!inviteUsername.trim() || inviting" @click="sendInvite">
            {{ inviting ? 'Отправка…' : 'Пригласить' }}
          </button>
        </div>
      </section>

      <!-- Основная валюта (только OWNER) -->
      <section v-if="session.isOwner" class="block">
        <h2 class="block-title">Основная валюта</h2>
        <p class="hint channel-note">
          Валюта учёта склада: в неё пересчитываются все суммы и аналитика.
          Введённые цены сохраняются и в исходной валюте.
        </p>
        <div class="cur-row">
          <button
            v-for="cur in CURRENCIES"
            :key="cur"
            class="cur-opt tap"
            :class="{ sel: cur === baseCurrency }"
            :disabled="currencyBusy"
            @click="changeBaseCurrency(cur)"
          >
            <span class="cur-code">{{ cur }}</span>
            <span class="cur-sym">{{ CURRENCY_SYMBOLS[cur] }}</span>
          </button>
        </div>
      </section>

      <!-- Автопостинг в канал (только OWNER) -->
      <section v-if="session.isOwner" class="block">
        <h2 class="block-title">Автопостинг в Telegram-канал</h2>
        <p class="hint channel-note">
          Выставленные вещи (статус «Выставлен») бот автоматически публикует в канал с фото,
          описанием, замерами и ценой. При продаже пост помечается «Продано».
        </p>
        <label class="lbl">Канал</label>
        <input
          v-model="channelInput"
          class="field"
          placeholder="@my_shop_channel или -100123456789"
          autocomplete="off"
        />
        <div class="channel-actions">
          <button class="btn-primary tap" :disabled="channelBusy" @click="saveChannel">
            {{ channelBusy ? '…' : 'Сохранить' }}
          </button>
          <button
            class="btn-secondary tap"
            :disabled="channelBusy || !channelSaved"
            @click="testChannel"
          >
            Проверить
          </button>
        </div>
        <div class="hint channel-hint">
          <div>⚠️ Сначала добавьте бота <b>@moi_shmotka_managerbot</b> администратором канала (право «Публикация сообщений»).</div>
          <div class="channel-variants">
            <div>• <b>Публичный</b> канал: <code>@имя_канала</code> или ссылка t.me/…</div>
            <div>• <b>Приватный</b> (без @): числовой ID вида <code>-100…</code></div>
            <div>Как узнать ID приватного: добавьте бота в канал — он пришлёт вам ID в личку.</div>
          </div>
        </div>

        <label class="wm-row">
          <span class="wm-main">
            <span class="wm-title">Водяной знак на фото</span>
            <span class="wm-sub">
              Подпись в углу фото, уходящих в канал. Оригиналы в складе не меняются.
            </span>
          </span>
          <input v-model="watermarkEnabled" type="checkbox" class="wm-check" />
        </label>
        <input
          v-if="watermarkEnabled"
          v-model="watermarkText"
          class="field"
          maxlength="60"
          placeholder="@ваш_канал (пусто — возьмём подпись выше)"
          autocomplete="off"
        />

        <button class="nav-row tap" @click="goTemplates">
          <span class="nav-row-main">
            <span class="nav-row-title">Шаблоны постов</span>
            <span class="nav-row-sub">{{ activeTemplateName }}</span>
          </span>
          <span class="nav-row-chevron" aria-hidden="true">›</span>
        </button>
      </section>

      <!-- Экспорт -->
      <section class="block">
        <h2 class="block-title">Данные</h2>
        <button class="btn-secondary tap" @click="exportCsv">Выгрузить в CSV</button>
        <p class="hint export-note">Отчёт придёт сообщением от бота.</p>
      </section>

      <div class="bottom-pad" />
    </div>
  </div>
</template>

<style scoped>
.settings {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.head {
  padding: calc(var(--safe-top) + 12px) 16px 10px;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
}
.content {
  flex: 1;
  overflow-y: auto;
  padding: 8px 16px calc(var(--nav-height) + var(--safe-bottom));
}
.block {
  padding: 16px 0;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.block-title {
  margin: 0 0 12px;
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--tg-theme-hint-color);
}
.role-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.role-pill {
  font-weight: 700;
  padding: 6px 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
}
.store-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.store-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: var(--tap);
  padding: 10px 14px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  border: 1px solid transparent;
  text-align: left;
}
.store-row.active {
  border-color: var(--tg-theme-link-color);
}
.store-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.store-name {
  font-weight: 600;
}
.store-role {
  font-size: 12px;
}
.check {
  color: var(--tg-theme-link-color);
}
.members,
.pending {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.member {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.m-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.m-name {
  font-weight: 600;
}
.m-uname {
  font-size: 12px;
}
.m-role {
  font-size: 13px;
  font-weight: 600;
  color: var(--tg-theme-hint-color);
}
.pending {
  margin-top: 6px;
}
.pending-row {
  opacity: 0.9;
}
.revoke {
  color: var(--tg-theme-destructive-text-color);
  font-weight: 700;
  font-size: 13px;
  padding: 8px;
}
.invite {
  margin-top: 16px;
}
.lbl {
  display: block;
  font-size: 13px;
  color: var(--tg-theme-hint-color);
  margin-bottom: 6px;
}
.field {
  width: 100%;
  min-height: var(--tap);
  padding: 0 12px;
  border-radius: var(--radius);
  border: 1px solid var(--tg-theme-secondary-bg-color);
  background: var(--tg-theme-secondary-bg-color);
  outline: none;
}
.role-select {
  display: flex;
  gap: 8px;
  margin: 10px 0;
}
.role-opt {
  flex: 1;
  min-height: var(--tap);
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  font-weight: 600;
  border: 1px solid transparent;
}
.role-opt.sel {
  border-color: var(--tg-theme-link-color);
  color: var(--tg-theme-link-color);
}
.btn-primary {
  width: 100%;
  min-height: var(--tap);
  border-radius: var(--radius);
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
  font-weight: 700;
}
.btn-primary:disabled {
  opacity: 0.5;
}
.btn-secondary {
  width: 100%;
  min-height: var(--tap);
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
  font-weight: 700;
}
.export-note {
  font-size: 12px;
  margin: 8px 0 0;
}
.channel-note {
  font-size: 12px;
  margin: 0 0 12px;
}
.channel-actions {
  display: flex;
  gap: 10px;
  margin-top: 10px;
}
.channel-actions .btn-primary,
.channel-actions .btn-secondary {
  flex: 1;
}
.channel-hint {
  font-size: 12px;
  margin: 10px 0 0;
}
.channel-variants {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.channel-hint code {
  font-family: ui-monospace, Menlo, monospace;
  background: var(--tg-theme-secondary-bg-color);
  padding: 1px 4px;
  border-radius: 4px;
}
.bottom-pad {
  height: 16px;
}
.nav-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: var(--tap);
  margin-top: 14px;
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  text-align: left;
}
.nav-row-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-row-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--tg-theme-text-color);
}
.nav-row-sub {
  font-size: 12px;
  color: var(--tg-theme-hint-color);
}
.nav-row-chevron {
  font-size: 22px;
  line-height: 1;
  color: var(--tg-theme-hint-color);
}
.cur-row {
  display: flex;
  gap: var(--gap);
}
.cur-opt {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  min-height: var(--tap);
  padding: 8px 4px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
}
.cur-opt.sel {
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
}
.cur-opt:disabled {
  opacity: 0.6;
}
.cur-code {
  font-size: 14px;
  font-weight: 700;
}
.cur-sym {
  font-size: 12px;
  opacity: 0.75;
}
.wm-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: var(--tap);
  margin-top: 14px;
}
.wm-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.wm-title {
  font-size: 15px;
  font-weight: 700;
}
.wm-sub {
  font-size: 12px;
  color: var(--tg-theme-hint-color);
  line-height: 1.4;
}
.wm-check {
  flex: none;
  width: 22px;
  height: 22px;
  accent-color: var(--tg-theme-button-color);
}
</style>
