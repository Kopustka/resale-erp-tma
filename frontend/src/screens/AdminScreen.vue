<script setup lang="ts">
/**
 * Админ-панель владельца склада.
 *
 * Экран открывается только владельцем, но полагаться на это нельзя: любой
 * пользователь может открыть мини-апп в браузере и позвать API напрямую.
 * Поэтому /api/v1/admin/* закрыт ролью на сервере, а скрытие пункта в
 * настройках — только чтобы не показывать сотруднику то, чем он не может
 * воспользоваться.
 */
import { computed, onMounted, ref, watch } from 'vue'
import { closeOverlay } from '@/app/navigation'
import { adminApi, storesApi } from '@/shared/api/endpoints'
import { ApiError } from '@/shared/api/http'
import { useSessionStore } from '@/stores/session'
import { useToastStore } from '@/stores/toast'
import { hapticImpact, hapticSelection } from '@/shared/telegram/webapp'
import type {
  ActivityEvent,
  AdminScope,
  MemberStats,
  OversightOut,
  PendingInvite,
  Role,
} from '@/shared/api/types'

const session = useSessionStore()
const toast = useToastStore()

type Pane = 'feed' | 'team'
const pane = ref<Pane>('feed')

/**
 * Панель показывает не только свой склад: владелец другого склада мог
 * открыть свою ленту по запросу. Выбранный склад участвует в каждом
 * запросе — сервер по нему же и проверяет право на просмотр.
 */
const scopes = ref<AdminScope[]>([])
const scopeId = ref<string | null>(null)
const currentScope = computed(
  () => scopes.value.find((s) => s.store_id === scopeId.value) ?? null,
)
const isOwnScope = computed(() => currentScope.value?.kind !== 'watch')

const watchList = ref<OversightOut[]>([])
const watchOpen = ref(false)
const watchName = ref('')
const watchBusy = ref(false)

const PERIODS = [
  { days: 7, label: '7 дней' },
  { days: 30, label: '30 дней' },
  { days: 365, label: 'Всё время' },
]
const days = ref(30)

const GROUPS = [
  { key: null as string | null, label: 'Всё' },
  { key: 'status', label: 'Статусы' },
  { key: 'items', label: 'Вещи' },
  { key: 'publishing', label: 'Публикации' },
  { key: 'settings', label: 'Настройки' },
]
const group = ref<string | null>(null)
const actorId = ref<string | null>(null)

const PAGE = 40
const events = ref<ActivityEvent[]>([])
const hasMore = ref(false)
const feedLoading = ref(false)
const feedMore = ref(false)
const feedError = ref<string | null>(null)

const members = ref<MemberStats[]>([])
const invites = ref<PendingInvite[]>([])
const teamLoading = ref(false)
const teamError = ref<string | null>(null)

const inviteOpen = ref(false)
const inviteName = ref('')
const inviteRole = ref<Exclude<Role, 'OWNER'>>('EMPLOYEE')
const inviting = ref(false)

function msg(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.message || fallback
  return e instanceof Error ? e.message : fallback
}

function who(a: { username: string | null; first_name: string | null }): string {
  return a.first_name || (a.username ? `@${a.username}` : 'Кто-то')
}

const ROLE_LABEL: Record<Role, string> = {
  OWNER: 'Владелец',
  EMPLOYEE: 'Сотрудник',
  ANALYST: 'Аналитик',
}

/** «5 мин назад» для свежего, дата — для старого: в ленте важна давность. */
function ago(iso: string): string {
  const t = new Date(iso).getTime()
  const mins = Math.floor((Date.now() - t) / 60000)
  if (mins < 1) return 'только что'
  if (mins < 60) return `${mins} мин назад`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} ч назад`
  const d = Math.floor(hours / 24)
  if (d < 7) return `${d} дн назад`
  return new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: 'short' })
}

function dayKey(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const yest = new Date(today.getTime() - 86400000)
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString()
  if (same(d, today)) return 'Сегодня'
  if (same(d, yest)) return 'Вчера'
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: 'long' })
}

/** Лента с разделителями по дням — так проще читать «что было вчера». */
const grouped = computed(() => {
  const out: { day: string; items: ActivityEvent[] }[] = []
  for (const e of events.value) {
    const k = dayKey(e.at)
    const last = out[out.length - 1]
    if (last && last.day === k) last.items.push(e)
    else out.push({ day: k, items: [e] })
  }
  return out
})

const filterName = computed(() => {
  if (!actorId.value) return null
  const m = members.value.find((x) => x.user_id === actorId.value)
  return m ? who(m) : null
})

async function loadScopes(): Promise<void> {
  try {
    scopes.value = await adminApi.scopes()
    if (!scopeId.value && scopes.value.length) {
      const own = scopes.value.find((s) => s.kind === 'own')
      scopeId.value = (own ?? scopes.value[0]).store_id
    }
  } catch {
    // Не критично: без списка панель работает по активному складу.
  }
}

async function loadWatchList(): Promise<void> {
  try {
    watchList.value = await adminApi.oversight()
  } catch {
    watchList.value = []
  }
}

// Счётчик запросов ленты. Фильтры переключают быстро — «7 дней», «30»,
// «всё время» подряд, — и без него в ленту записывался тот ответ, который
// пришёл последним, а не тот, что соответствует выбранному чипу.
let feedSeq = 0

async function loadFeed(): Promise<void> {
  const seq = ++feedSeq
  feedLoading.value = true
  feedError.value = null
  try {
    const page = await adminApi.activity({
      storeId: scopeId.value,
      userId: actorId.value,
      group: group.value,
      days: days.value,
      limit: PAGE,
      offset: 0,
    })
    if (seq !== feedSeq) return
    events.value = page.events
    hasMore.value = page.has_more
  } catch (e) {
    if (seq !== feedSeq) return
    feedError.value = msg(e, 'Не удалось загрузить ленту')
  } finally {
    if (seq === feedSeq) feedLoading.value = false
  }
}

async function loadMore(): Promise<void> {
  if (feedMore.value || !hasMore.value) return
  const seq = feedSeq
  feedMore.value = true
  try {
    const page = await adminApi.activity({
      storeId: scopeId.value,
      userId: actorId.value,
      group: group.value,
      days: days.value,
      limit: PAGE,
      offset: events.value.length,
    })
    if (seq !== feedSeq) return
    events.value.push(...page.events)
    hasMore.value = page.has_more
  } catch (e) {
    if (seq !== feedSeq) return
    toast.error(msg(e, 'Не удалось догрузить'))
  } finally {
    feedMore.value = false
  }
}

async function loadTeam(): Promise<void> {
  teamLoading.value = true
  teamError.value = null
  try {
    const ov = await adminApi.team(days.value, scopeId.value)
    members.value = ov.members
    invites.value = ov.invites
  } catch (e) {
    teamError.value = msg(e, 'Не удалось загрузить команду')
  } finally {
    teamLoading.value = false
  }
}

onMounted(async () => {
  await loadScopes()
  void loadFeed()
  // Команду тянем сразу: её имена нужны подписи фильтра в ленте.
  void loadTeam()
  void loadWatchList()
})

watch([days, group, actorId], () => void loadFeed())
watch(days, () => void loadTeam())
watch(scopeId, () => {
  // Смена склада сбрасывает фильтр по человеку: на другом складе его нет.
  // Само присваивание уже поднимет watch выше и перезагрузит ленту —
  // второй вызов loadFeed() здесь давал два одинаковых запроса на каждое
  // переключение склада. Если фильтр и так был пуст, watch не сработает,
  // поэтому грузим вручную только в этом случае.
  const hadActor = actorId.value !== null
  actorId.value = null
  events.value = []
  if (!hadActor) void loadFeed()
  void loadTeam()
})

function pickActor(id: string | null): void {
  hapticSelection()
  actorId.value = actorId.value === id ? null : id
  pane.value = 'feed'
}

async function sendInvite(): Promise<void> {
  const name = inviteName.value.replace(/^@/, '').trim()
  if (!name || inviting.value) return
  inviting.value = true
  try {
    const inv = await session.createInvite(name, inviteRole.value)
    hapticImpact('medium')
    toast.success(
      inv.status === 'PENDING'
        ? `@${name} добавлен — доступ включится, когда он напишет боту /start`
        : `@${name} подключён к складу`,
    )
    inviteName.value = ''
    inviteOpen.value = false
    await loadTeam()
  } catch (e) {
    toast.error(msg(e, 'Не удалось пригласить'))
  } finally {
    inviting.value = false
  }
}

async function revoke(inv: PendingInvite): Promise<void> {
  try {
    await storesApi.revokeInvite(inv.id)
    invites.value = invites.value.filter((i) => i.id !== inv.id)
    toast.success('Приглашение отозвано')
  } catch (e) {
    toast.error(msg(e, 'Не удалось отозвать'))
  }
}

async function requestWatch(): Promise<void> {
  const name = watchName.value.replace(/^@/, '').trim()
  if (!name || watchBusy.value) return
  watchBusy.value = true
  try {
    const res = await adminApi.requestOversight(name)
    hapticImpact('medium')
    toast.success(
      res.status === 'ACTIVE'
        ? `Склад @${name} подключён`
        : `@${name} ещё не запускал бота — подключится при первом /start`,
    )
    watchName.value = ''
    watchOpen.value = false
    await loadWatchList()
  } catch (e) {
    toast.error(msg(e, 'Не удалось отправить запрос'))
  } finally {
    watchBusy.value = false
  }
}

async function dropWatch(w: OversightOut): Promise<void> {
  try {
    await adminApi.dropOversight(w.id)
    watchList.value = watchList.value.filter((x) => x.id !== w.id)
    toast.success('Больше не наблюдаем')
    await loadScopes()
    if (scopeId.value && !scopes.value.some((x) => x.store_id === scopeId.value)) {
      scopeId.value = scopes.value[0]?.store_id ?? null
    }
  } catch (e) {
    toast.error(msg(e, 'Не удалось отключить'))
  }
}
</script>

<template>
  <div class="admin">
    <header class="head">
      <button class="close tap" aria-label="Закрыть" @click="closeOverlay">✕</button>
      <h2 class="title">Админ-панель</h2>
    </header>

    <div class="panes">
      <button :class="['pane-tab', { on: pane === 'feed' }]" @click="pane = 'feed'">
        Действия
      </button>
      <button :class="['pane-tab', { on: pane === 'team' }]" @click="pane = 'team'">
        Команда
      </button>
    </div>

    <div v-if="scopes.length > 1" class="chips scope-row">
      <button
        v-for="sc in scopes"
        :key="sc.store_id"
        :class="['chip', { on: scopeId === sc.store_id }]"
        @click="scopeId = sc.store_id"
      >
        {{ sc.kind === 'watch' ? '👀 ' : '' }}{{ sc.name }}
      </button>
    </div>

    <p v-if="!isOwnScope" class="watch-note">
      Чужой склад. Видна лента действий и счётчики; закупки и прибыль не
      показываются, изменить здесь ничего нельзя.
    </p>

    <div class="chips periods">
      <button
        v-for="p in PERIODS"
        :key="p.days"
        :class="['chip', { on: days === p.days }]"
        @click="days = p.days"
      >
        {{ p.label }}
      </button>
    </div>

    <!-- ------------------------------ Лента ------------------------------ -->
    <template v-if="pane === 'feed'">
      <div class="chips">
        <button
          v-for="g in GROUPS"
          :key="String(g.key)"
          :class="['chip', { on: group === g.key }]"
          @click="group = g.key"
        >
          {{ g.label }}
        </button>
      </div>

      <div v-if="filterName" class="active-filter">
        Только: <b>{{ filterName }}</b>
        <button class="link tap" @click="actorId = null">сбросить</button>
      </div>

      <div class="scroll">
        <p v-if="feedLoading" class="hint pad">Загрузка…</p>
        <p v-else-if="feedError" class="negative pad">{{ feedError }}</p>
        <p v-else-if="events.length === 0" class="hint pad">
          За выбранный период действий не было.
        </p>

        <template v-else>
          <section v-for="bucket in grouped" :key="bucket.day" class="day">
            <h3 class="day-title">{{ bucket.day }}</h3>
            <ul class="feed">
              <li v-for="e in bucket.items" :key="e.id" class="event">
                <span class="icon" aria-hidden="true">{{ e.icon }}</span>
                <div class="body">
                  <p class="line">
                    <button class="actor tap" @click="pickActor(e.actor.user_id)">
                      {{ who(e.actor) }}
                    </button>
                    <span class="what">{{ e.title.toLowerCase() }}</span>
                  </p>
                  <p v-if="e.summary" class="detail">{{ e.summary }}</p>
                </div>
                <time class="when" :datetime="e.at">{{ ago(e.at) }}</time>
              </li>
            </ul>
          </section>

          <button
            v-if="hasMore"
            class="btn-secondary tap more"
            :disabled="feedMore"
            @click="loadMore"
          >
            {{ feedMore ? '…' : 'Показать ещё' }}
          </button>
        </template>
      </div>
    </template>

    <!-- ----------------------------- Команда ----------------------------- -->
    <div v-else class="scroll">
      <p v-if="teamLoading" class="hint pad">Загрузка…</p>
      <p v-else-if="teamError" class="negative pad">{{ teamError }}</p>

      <template v-else>
        <ul class="team">
          <li v-for="m in members" :key="m.user_id" class="member">
            <div class="member-head">
              <div>
                <p class="name">{{ who(m) }}</p>
                <p class="sub">
                  {{ ROLE_LABEL[m.role] }}
                  <template v-if="m.username"> · @{{ m.username }}</template>
                </p>
              </div>
              <span class="ops">{{ m.operations }}</span>
            </div>

            <div class="metrics">
              <div class="metric">
                <span class="v">{{ m.items_added }}</span>
                <span class="k">добавил</span>
              </div>
              <div class="metric">
                <span class="v">{{ m.listed }}</span>
                <span class="k">выставил</span>
              </div>
              <div class="metric">
                <span class="v">{{ m.shipped }}</span>
                <span class="k">отправил</span>
              </div>
              <div class="metric">
                <span class="v">{{ m.discounts }}</span>
                <span class="k">скидок</span>
              </div>
            </div>

            <div class="member-foot">
              <span class="hint">
                {{ m.last_action_at ? `Последнее действие ${ago(m.last_action_at)}` : 'Пока не активен' }}
              </span>
              <button class="link tap" @click="pickActor(m.user_id)">Действия</button>
            </div>
          </li>
        </ul>

        <section v-if="isOwnScope && invites.length" class="block">
          <h3 class="sec-title">Ждут первого запуска бота</h3>
          <ul class="team">
            <li v-for="i in invites" :key="i.id" class="member compact">
              <div class="member-head">
                <div>
                  <p class="name">@{{ i.username }}</p>
                  <p class="sub">{{ ROLE_LABEL[i.role] }} · приглашение отправлено</p>
                </div>
                <button class="link negative tap" @click="revoke(i)">Отозвать</button>
              </div>
            </li>
          </ul>
        </section>

        <section v-if="isOwnScope" class="block">
          <template v-if="inviteOpen">
            <label class="lbl">Telegram-юзернейм</label>
            <input
              v-model="inviteName"
              class="field"
              placeholder="@username"
              autocomplete="off"
            />
            <label class="lbl">Роль</label>
            <div class="chips">
              <button
                :class="['chip', { on: inviteRole === 'EMPLOYEE' }]"
                @click="inviteRole = 'EMPLOYEE'"
              >
                Сотрудник
              </button>
              <button
                :class="['chip', { on: inviteRole === 'ANALYST' }]"
                @click="inviteRole = 'ANALYST'"
              >
                Аналитик
              </button>
            </div>
            <p class="note">
              <b>Сотрудник</b> ведёт склад: добавляет вещи, меняет статусы, публикует —
              но не видит закупки и прибыль.<br />
              <b>Аналитик</b> видит деньги и отчёты, но ничего не меняет.
            </p>
            <div class="row">
              <button
                class="btn-primary tap"
                :disabled="!inviteName.trim() || inviting"
                @click="sendInvite"
              >
                {{ inviting ? '…' : 'Пригласить' }}
              </button>
              <button class="btn-secondary tap" @click="inviteOpen = false">Отмена</button>
            </div>
          </template>
          <button v-else class="btn-secondary tap" @click="inviteOpen = true">
            + Добавить человека
          </button>
        </section>

        <!-- Чужие склады, за которыми ведём наблюдение -->
        <section v-if="isOwnScope" class="block">
          <h3 class="sec-title">Чужие склады в этой панели</h3>
          <p class="note">
            Если человек ведёт свой склад отдельно, его ленту можно подключить
            сюда по юзернейму. Видны действия и счётчики; закупки и прибыль —
            нет. Если он ещё не запускал бота, склад подключится сам при первом
            его <code>/start</code>.
          </p>

          <ul v-if="watchList.length" class="team">
            <li v-for="w in watchList" :key="w.id" class="member compact">
              <div class="member-head">
                <div>
                  <p class="name">@{{ w.target_username }}</p>
                  <p class="sub">
                    {{
                      w.status === 'ACTIVE'
                        ? `Склад «${w.store_name}»`
                        : 'Ещё не запускал бота — подключится при /start'
                    }}
                  </p>
                </div>
                <button class="link negative tap" @click="dropWatch(w)">
                  {{ w.status === 'ACTIVE' ? 'Отключить' : 'Отменить' }}
                </button>
              </div>
            </li>
          </ul>

          <template v-if="watchOpen">
            <label class="lbl">Telegram-юзернейм</label>
            <input
              v-model="watchName"
              class="field"
              placeholder="@username"
              autocomplete="off"
            />
            <div class="row">
              <button
                class="btn-primary tap"
                :disabled="!watchName.trim() || watchBusy"
                @click="requestWatch"
              >
                {{ watchBusy ? '…' : 'Подключить' }}
              </button>
              <button class="btn-secondary tap" @click="watchOpen = false">Отмена</button>
            </div>
          </template>
          <button v-else class="btn-secondary tap wide" @click="watchOpen = true">
            + Подключить чужой склад
          </button>
        </section>
      </template>
    </div>
  </div>
</template>

<style scoped>
.admin {
  position: fixed;
  inset: 0;
  z-index: 120;
  background: var(--tg-theme-bg-color);
  display: flex;
  flex-direction: column;
}
.head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: calc(var(--safe-top) + 10px) 12px 10px;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.close {
  display: flex;
  align-items: center;
  justify-content: center;
}
.title {
  font-size: 15.5px;
  font-weight: 700;
  margin: 0;
}
.panes {
  display: flex;
  padding: 0 10.5px;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.pane-tab {
  flex: 1;
  padding: 10.5px 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--tg-theme-hint-color);
  border-bottom: 2px solid transparent;
}
.pane-tab.on {
  color: var(--tg-theme-text-color);
  border-bottom-color: var(--tg-theme-link-color);
}
.chips {
  display: flex;
  gap: 7px;
  overflow-x: auto;
  padding: 8.5px 14px;
  scrollbar-width: none;
}
.chips::-webkit-scrollbar {
  display: none;
}
.periods {
  padding-bottom: 2px;
}
.chip {
  flex: 0 0 auto;
  padding: 5px 10.5px;
  border-radius: 869px;
  font-size: 11.5px;
  font-weight: 600;
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-hint-color);
}
.chip.on {
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
}
.active-filter {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 14px 7px;
  font-size: 11.5px;
  color: var(--tg-theme-hint-color);
}
.scroll {
  flex: 1;
  overflow-y: auto;
  padding: 4px 16px calc(var(--safe-bottom) + 24px);
  -webkit-overflow-scrolling: touch;
}
.pad {
  padding: 21px 0;
  text-align: center;
}
.hint {
  color: var(--tg-theme-hint-color);
}
.negative {
  color: var(--accent-negative);
}
.day-title {
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--tg-theme-hint-color);
  margin: 14px 0 5px;
}
.feed,
.team {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.event {
  display: flex;
  align-items: flex-start;
  gap: 8.5px;
  padding: 8.5px 10.5px;
  border-radius: var(--radius);
  background: var(--tg-theme-secondary-bg-color);
}
.icon {
  font-size: 14px;
  line-height: 17.5px;
  flex: 0 0 auto;
}
.body {
  flex: 1;
  min-width: 0;
}
.line {
  margin: 0;
  font-size: 12px;
  line-height: 17.5px;
}
.actor {
  font-weight: 700;
  color: var(--tg-theme-link-color);
}
.what {
  margin-left: 3.5px;
}
.detail {
  margin: 2px 0 0;
  font-size: 11.5px;
  color: var(--tg-theme-hint-color);
  overflow-wrap: anywhere;
}
.when {
  flex: 0 0 auto;
  font-size: 9.5px;
  color: var(--tg-theme-hint-color);
  padding-top: 2.5px;
}
.more {
  margin: 14px 0 0;
  width: 100%;
}
.team {
  gap: var(--gap);
  margin-top: 7px;
}
.member {
  background: var(--tg-theme-secondary-bg-color);
  border-radius: var(--radius);
  padding: 10.5px;
}
.member.compact {
  padding: 8.5px 10.5px;
}
.member-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 7px;
}
.name {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
}
.sub {
  margin: 2px 0 0;
  font-size: 10.5px;
  color: var(--tg-theme-hint-color);
}
.ops {
  font-size: 17.5px;
  font-weight: 700;
  color: var(--tg-theme-link-color);
}
.metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 3.5px;
  margin-top: 8.5px;
}
.metric {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 5px 2px;
  border-radius: 7px;
  background: var(--tg-theme-bg-color);
}
.metric .v {
  font-size: 14px;
  font-weight: 700;
}
.metric .k {
  font-size: 8.5px;
  color: var(--tg-theme-hint-color);
}
.member-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 7px;
  margin-top: 8.5px;
  font-size: 10.5px;
}
.block {
  padding: 14px 0 3.5px;
}
.sec-title {
  font-size: 11.5px;
  font-weight: 700;
  margin: 0 0 7px;
  color: var(--tg-theme-hint-color);
}
.note {
  font-size: 10.5px;
  color: var(--tg-theme-hint-color);
  margin: 7px 0 0;
  line-height: 1.5;
}
.row {
  display: flex;
  gap: 7px;
  margin-top: 10.5px;
}
.scope-row {
  padding-bottom: 0;
}
.watch-note {
  margin: 0;
  padding: 5px 14px 0;
  font-size: 10.5px;
  line-height: 1.4;
  color: var(--tg-theme-hint-color);
}
.wide {
  width: 100%;
  margin-top: 10.5px;
}
</style>
