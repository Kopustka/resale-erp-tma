<script setup lang="ts">
/**
 * Каналы автопостинга: список, добавление, включение/выключение,
 * подпись под постами конкретного канала, проверка и удаление.
 */
import { onMounted, ref } from 'vue'
import { closeOverlay } from '@/app/navigation'
import { channelsApi } from '@/shared/api/endpoints'
import { ApiError } from '@/shared/api/http'
import { useToastStore } from '@/stores/toast'
import { hapticImpact, hapticSelection } from '@/shared/telegram/webapp'
import type { Channel } from '@/shared/api/types'

const toast = useToastStore()

const list = ref<Channel[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const busyId = ref<string | null>(null)
const confirmDeleteId = ref<string | null>(null)

const adding = ref(false)
const newChat = ref('')
const newTitle = ref('')
const saving = ref(false)

function msg(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.message || fallback
  return e instanceof Error ? e.message : fallback
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    list.value = await channelsApi.list()
  } catch (e) {
    error.value = msg(e, 'Не удалось загрузить каналы')
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function addChannel(): Promise<void> {
  const chat = newChat.value.trim()
  if (!chat || saving.value) return
  saving.value = true
  try {
    const created = await channelsApi.create({
      chat_id: chat,
      title: newTitle.value.trim() || null,
    })
    list.value.push(created)
    newChat.value = ''
    newTitle.value = ''
    adding.value = false
    hapticImpact('medium')
    toast.success('Канал добавлен')
  } catch (e) {
    toast.error(msg(e, 'Не удалось добавить канал'))
  } finally {
    saving.value = false
  }
}

async function toggle(ch: Channel): Promise<void> {
  busyId.value = ch.id
  hapticSelection()
  try {
    const fresh = await channelsApi.update(ch.id, { enabled: !ch.enabled })
    Object.assign(ch, fresh)
  } catch (e) {
    toast.error(msg(e, 'Не удалось изменить канал'))
  } finally {
    busyId.value = null
  }
}

async function saveSignature(ch: Channel, value: string): Promise<void> {
  const next = value.trim() || null
  if (next === ch.signature) return
  try {
    const fresh = await channelsApi.update(ch.id, { signature: next })
    Object.assign(ch, fresh)
    toast.success('Подпись сохранена')
  } catch (e) {
    toast.error(msg(e, 'Не удалось сохранить подпись'))
  }
}

async function test(ch: Channel): Promise<void> {
  busyId.value = ch.id
  try {
    await channelsApi.test(ch.id)
    toast.success('Тестовое сообщение отправлено ✅')
  } catch (e) {
    toast.error(msg(e, 'Не удалось отправить'))
  } finally {
    busyId.value = null
  }
}

async function remove(ch: Channel): Promise<void> {
  confirmDeleteId.value = null
  try {
    await channelsApi.remove(ch.id)
    list.value = list.value.filter((c) => c.id !== ch.id)
    hapticImpact('medium')
    toast.success('Канал удалён')
  } catch (e) {
    toast.error(msg(e, 'Не удалось удалить канал'))
  }
}
</script>

<template>
  <div class="channels">
    <header class="head">
      <button class="close tap" aria-label="Закрыть" @click="closeOverlay">
        <svg viewBox="0 0 24 24" width="24" height="24">
          <path fill="currentColor" d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" />
        </svg>
      </button>
      <h1 class="title">Каналы</h1>
    </header>

    <div class="scroll no-scrollbar">
      <section class="block">
        <p class="note">
          Выставленная вещь публикуется во все включённые каналы. При продаже
          пост помечается «Продано» в каждом из них.
        </p>

        <p v-if="loading" class="hint">Загрузка…</p>
        <p v-else-if="error" class="negative">{{ error }}</p>

        <div v-else-if="!list.length" class="empty">
          <p>Каналов пока нет.</p>
          <p class="note">
            Добавьте бота <b>@moi_shmotka_managerbot</b> администратором канала
            с правом публикации, затем укажите канал здесь.
          </p>
        </div>

        <ul v-else class="list">
          <li v-for="ch in list" :key="ch.id" class="card" :class="{ off: !ch.enabled }">
            <div class="card-head">
              <div class="card-id">
                <span class="chat">{{ ch.chat_id }}</span>
                <span v-if="ch.title" class="ch-title">{{ ch.title }}</span>
                <span class="hint posts">{{ ch.posts_count }} публ.</span>
              </div>
              <input
                type="checkbox"
                class="switch"
                :checked="ch.enabled"
                :disabled="busyId === ch.id"
                @change="toggle(ch)"
              />
            </div>

            <label class="lbl">Подпись под постами</label>
            <input
              class="field"
              :value="ch.signature ?? ''"
              maxlength="120"
              placeholder="Пусто — возьмём общую из настроек"
              autocomplete="off"
              @change="saveSignature(ch, ($event.target as HTMLInputElement).value)"
            />

            <div class="card-actions">
              <button class="link tap" :disabled="busyId === ch.id" @click="test(ch)">
                Проверить
              </button>
              <template v-if="confirmDeleteId === ch.id">
                <button class="link negative tap" @click="remove(ch)">Точно удалить</button>
                <button class="link tap" @click="confirmDeleteId = null">Нет</button>
              </template>
              <button v-else class="link negative tap" @click="confirmDeleteId = ch.id">
                Удалить
              </button>
            </div>
          </li>
        </ul>
      </section>

      <section class="block">
        <template v-if="adding">
          <label class="lbl">Канал</label>
          <input
            v-model="newChat"
            class="field"
            placeholder="@my_shop_channel или -100123456789"
            autocomplete="off"
          />
          <label class="lbl">Название (необязательно)</label>
          <input v-model="newTitle" class="field" placeholder="Основной" autocomplete="off" />
          <div class="row">
            <button class="btn-primary tap" :disabled="!newChat.trim() || saving" @click="addChannel">
              {{ saving ? '…' : 'Добавить' }}
            </button>
            <button class="btn-secondary tap" @click="adding = false">Отмена</button>
          </div>
          <p class="note">
            Приватный канал — числовой ID вида <code>-100…</code>. Добавьте туда бота,
            и он пришлёт ID в личку.
          </p>
        </template>
        <button v-else class="btn-secondary tap" @click="adding = true">+ Добавить канал</button>
      </section>
    </div>
  </div>
</template>

<style scoped>
.channels {
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
  gap: 8px;
  padding: calc(var(--safe-top) + 10px) 12px 10px;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.close {
  display: flex;
  align-items: center;
  justify-content: center;
}
.title {
  font-size: 18px;
  font-weight: 700;
  margin: 0;
}
.scroll {
  flex: 1;
  overflow-y: auto;
  padding: 8px 16px calc(var(--safe-bottom) + 24px);
}
.block {
  padding: 12px 0;
  border-bottom: 1px solid var(--tg-theme-secondary-bg-color);
}
.block:last-child {
  border-bottom: none;
}
.note {
  font-size: 12px;
  color: var(--tg-theme-hint-color);
  margin: 8px 0 0;
  line-height: 1.4;
}
.hint {
  color: var(--tg-theme-hint-color);
}
.negative {
  color: var(--accent-negative);
}
.list {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--gap);
}
.card {
  background: var(--tg-theme-secondary-bg-color);
  border-radius: var(--radius);
  padding: 12px;
}
.card.off {
  opacity: 0.55;
}
.card-head {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.card-id {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.chat {
  font-size: 15px;
  font-weight: 700;
  word-break: break-all;
}
.ch-title {
  font-size: 13px;
  color: var(--tg-theme-hint-color);
}
.posts {
  font-size: 12px;
}
.switch {
  flex: none;
  width: 22px;
  height: 22px;
  accent-color: var(--tg-theme-button-color);
}
.lbl {
  display: block;
  font-size: 12px;
  color: var(--tg-theme-hint-color);
  margin: 10px 0 4px;
}
.field {
  width: 100%;
  min-height: var(--tap);
  padding: 10px 12px;
  border-radius: var(--radius);
  background: var(--tg-theme-bg-color);
  color: var(--tg-theme-text-color);
  border: 1px solid transparent;
}
.card-actions {
  display: flex;
  gap: 16px;
  margin-top: 10px;
}
.link {
  min-height: var(--tap);
  font-size: 14px;
  font-weight: 700;
  color: var(--tg-theme-link-color);
}
.link.negative {
  color: var(--accent-negative);
}
.link:disabled {
  opacity: 0.5;
}
.row {
  display: flex;
  gap: var(--gap);
  margin-top: 12px;
}
.btn-primary,
.btn-secondary {
  flex: 1;
  min-height: var(--tap);
  border-radius: var(--radius);
  font-size: 15px;
  font-weight: 700;
}
.btn-primary {
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
}
.btn-primary:disabled {
  opacity: 0.6;
}
.btn-secondary {
  background: var(--tg-theme-secondary-bg-color);
  color: var(--tg-theme-text-color);
}
.empty {
  padding: 8px 0;
}
</style>
