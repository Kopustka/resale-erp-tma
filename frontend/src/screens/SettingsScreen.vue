<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { openAdmin } from '@/app/navigation'
import { useSessionStore } from '@/stores/session'
import { useItemsStore } from '@/stores/items'
import { useAnalyticsStore } from '@/stores/analytics'
import { useToastStore } from '@/stores/toast'
import { CURRENCIES, CURRENCY_SYMBOLS, type Currency, type Role } from '@/shared/api/types'
import { storesApi } from '@/shared/api/endpoints'
import { hapticSelection } from '@/shared/telegram/webapp'

const session = useSessionStore()
const items = useItemsStore()
const analytics = useAnalyticsStore()
const toast = useToastStore()

/** Подпись под строкой перехода: какой шаблон сейчас используется. */
function goAdmin(): void {
  hapticSelection()
  openAdmin()
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
onMounted(() => {
  baseCurrency.value = session.baseCurrency
  if (session.isOwner) {
    void session.fetchMembers()
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
        <div class="card role-line">
          <span class="role-label">Ваша роль</span>
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
              <span class="store-role">{{ ROLE_LABELS[s.role] }}</span>
            </div>
            <span v-if="s.id === session.currentStoreId" class="check">
              <svg viewBox="0 0 24 24" width="20" height="20">
                <path fill="currentColor" d="m9 16.2-3.5-3.5-1.4 1.4L9 19 20 8l-1.4-1.4z" />
              </svg>
            </span>
          </button>
        </div>
        <p v-else class="empty">Нет доступных складов.</p>
      </section>

      <!-- Команда (только OWNER) -->
      <section v-if="session.isOwner" class="block">
        <h2 class="block-title">Команда</h2>

        <button class="nav-row tap" @click="goAdmin">
          <span class="nav-row-main">
            <span class="nav-row-title">Админ-панель</span>
            <span class="nav-row-sub">Кто что делал: лента действий и статистика</span>
          </span>
          <span class="nav-row-chevron" aria-hidden="true">›</span>
        </button>

        <div v-if="session.membersLoading" class="loading">Загрузка участников…</div>
        <div v-else class="members">
          <div v-for="m in session.members" :key="m.user_id" class="member">
            <div class="m-info">
              <span class="m-name">{{ m.first_name || m.username || '—' }}</span>
              <span v-if="m.username" class="m-uname">@{{ m.username }}</span>
            </div>
            <span class="m-role">{{ ROLE_LABELS[m.role] }}</span>
          </div>
        </div>

        <!-- Pending инвайты этой сессии -->
        <div v-if="session.pendingInvites.length" class="members pending">
          <div v-for="inv in session.pendingInvites" :key="inv.id" class="member">
            <div class="m-info">
              <span class="m-name">@{{ inv.username }}</span>
              <span class="m-uname">ожидает · {{ ROLE_LABELS[inv.role] }}</span>
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
              class="role-opt tap"
              :class="{ sel: inviteRole === 'EMPLOYEE' }"
              @click="inviteRole = 'EMPLOYEE'"
            >
              Сотрудник
            </button>
            <button
              class="role-opt tap"
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
        <p class="note">
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

      <!-- Экспорт -->
      <section class="block">
        <h2 class="block-title">Данные</h2>
        <button class="btn-secondary tap" @click="exportCsv">Выгрузить в CSV</button>
        <p class="note export-note">Отчёт придёт сообщением от бота.</p>
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
  background: var(--ink-0);
  color: var(--fg-0);
}

/* --- Шапка --- */
.head {
  padding: calc(var(--safe-top) + 12px) var(--pad) 8px;
  background: var(--ink-0);
}
.title {
  margin: 0;
  font-size: 22px;
  font-weight: 650;
  letter-spacing: -0.01em;
}

/* --- Секции --- */
.content {
  flex: 1;
  overflow-y: auto;
  padding: 8px var(--pad) calc(var(--nav-height) + var(--safe-bottom));
}
.block {
  margin-bottom: 20px;
}
.block-title {
  margin: 0 0 10px;
  font-size: 17px;
  font-weight: 650;
  color: var(--fg-0);
}
.card {
  padding: 14px 16px;
  border-radius: var(--r-card);
  background: var(--ink-1);
}
.note {
  margin: 0 0 12px;
  font-size: 12.5px;
  line-height: 1.45;
  color: var(--fg-2);
}
.empty,
.loading {
  margin: 0;
  font-size: 14px;
  color: var(--fg-1);
}
.loading {
  margin-top: 12px;
}

/* --- Роль --- */
.role-line {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.role-label {
  font-size: 14px;
  color: var(--fg-1);
}
.role-pill {
  padding: 7px 14px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
  color: var(--fg-0);
  font-size: 13.5px;
  font-weight: 650;
}

/* --- Склады --- */
.store-list {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
}
.store-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  min-height: 60px;
  padding: 10px 16px;
  border-radius: var(--r-card);
  background: var(--ink-1);
  color: var(--fg-0);
  text-align: left;
}
.store-row.active {
  background: var(--ink-3);
}
.store-row:disabled {
  opacity: 0.6;
}
.store-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.store-name {
  font-size: 15.5px;
  font-weight: 650;
}
.store-role {
  font-size: 12.5px;
  color: var(--fg-2);
}
.check {
  flex: none;
  display: flex;
  color: var(--brand);
}

/* --- Строка-переход --- */
.nav-row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 60px;
  padding: 10px 16px;
  border-radius: var(--r-card);
  background: var(--ink-1);
  text-align: left;
}
.nav-row:active {
  background: var(--ink-2);
}
.nav-row-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.nav-row-title {
  font-size: 15.5px;
  font-weight: 650;
  color: var(--fg-0);
}
.nav-row-sub {
  font-size: 12.5px;
  line-height: 1.35;
  color: var(--fg-2);
}
.nav-row-chevron {
  flex: none;
  font-size: 24px;
  line-height: 1;
  color: var(--fg-2);
}

/* --- Участники --- */
.members {
  margin-top: var(--gap);
  padding: 2px 16px;
  border-radius: var(--r-card);
  background: var(--ink-1);
}
.members:empty {
  display: none;
}
.member {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
}
.member + .member {
  border-top: 1px solid var(--ink-2);
}
.m-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.m-name {
  font-size: 15px;
  font-weight: 600;
}
.m-uname {
  font-size: 12.5px;
  color: var(--fg-2);
}
.m-role {
  flex: none;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg-1);
}
.revoke {
  flex: none;
  padding: 8px 14px;
  border-radius: var(--r-pill);
  background: var(--ink-2);
  color: var(--danger);
  font-size: 13px;
  font-weight: 650;
}

/* --- Приглашение --- */
.invite {
  margin-top: var(--gap);
}
.lbl {
  display: block;
  margin-bottom: 6px;
  font-size: 12.5px;
  color: var(--fg-2);
}
.field {
  width: 100%;
  height: 50px;
  padding: 0 14px;
  border: none;
  border-radius: var(--r-field);
  background: var(--ink-1);
  color: var(--fg-0);
  font-size: 16px;
  outline: none;
  -webkit-appearance: none;
  appearance: none;
}
.field::placeholder {
  color: var(--fg-2);
}
.field:focus {
  background: var(--ink-2);
}
.role-select {
  display: flex;
  gap: 8px;
  margin: 10px 0 12px;
}
.role-opt {
  flex: 1;
  height: 44px;
  min-height: 44px;
  border-radius: var(--r-pill);
  background: var(--ink-1);
  color: var(--fg-1);
  font-size: 14px;
  font-weight: 650;
}
.role-opt.sel {
  background: var(--brand);
  color: var(--brand-ink);
}

/* --- Кнопки --- */
.btn-primary,
.btn-secondary {
  width: 100%;
  height: 50px;
  min-height: 50px;
  border-radius: var(--r-field);
  font-size: 16px;
  font-weight: 650;
}
.btn-primary {
  background: var(--brand);
  color: var(--brand-ink);
}
.btn-primary:disabled {
  opacity: 0.45;
}
.btn-secondary {
  background: var(--ink-2);
  color: var(--fg-0);
}

/* --- Валюта --- */
.cur-row {
  display: flex;
  gap: 8px;
}
.cur-opt {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 46px;
  min-height: 46px;
  border-radius: var(--r-pill);
  background: var(--ink-1);
  color: var(--fg-1);
}
.cur-opt.sel {
  background: var(--brand);
  color: var(--brand-ink);
}
.cur-opt:disabled {
  opacity: 0.6;
}
.cur-code {
  font-size: 14px;
  font-weight: 650;
}
.cur-sym {
  font-size: 13px;
  opacity: 0.75;
}

.export-note {
  margin: 10px 0 0;
}
.bottom-pad {
  height: 16px;
}
</style>
