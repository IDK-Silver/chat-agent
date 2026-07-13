<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Plus, RefreshCw } from 'lucide-vue-next'
import {
  beginClaudeLogin,
  completeClaudeLogin,
  fetchClaudeAccounts,
  promoteClaudeAccount,
  removeClaudeAccount,
  type ClaudeAccount,
  type ClaudeAccountsResponse,
  type ClaudeLoginBegin,
} from '@/api/client'

const REFRESH_MS = 180_000

const data = ref<ClaudeAccountsResponse | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const actionBusy = ref(false)
const actionError = ref<string | null>(null)
let timer: number | undefined

const login = ref<ClaudeLoginBegin | null>(null)
const loginCode = ref('')
const loginBusy = ref(false)
const loginError = ref<string | null>(null)

async function refresh(force = false) {
  if (refreshing.value) return
  refreshing.value = true
  try {
    data.value = await fetchClaudeAccounts(force)
  } catch {
    data.value = { available: false, accounts: [], models: [], error: 'request failed' }
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

async function runAction(action: () => Promise<unknown>) {
  if (actionBusy.value) return
  actionBusy.value = true
  actionError.value = null
  try {
    await action()
    await refresh(true)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'request failed'
  } finally {
    actionBusy.value = false
  }
}

function promote(acct: ClaudeAccount) {
  runAction(() => promoteClaudeAccount(acct.id))
}

function remove(acct: ClaudeAccount) {
  const label = acct.account?.email ?? acct.id
  if (!window.confirm(`Remove ${label} from the proxy token store?`)) return
  runAction(() => removeClaudeAccount(acct.id))
}

async function startLogin() {
  if (loginBusy.value) return
  loginBusy.value = true
  loginError.value = null
  try {
    login.value = await beginClaudeLogin()
    loginCode.value = ''
  } catch (err) {
    loginError.value = err instanceof Error ? err.message : 'request failed'
  } finally {
    loginBusy.value = false
  }
}

async function completeLogin() {
  if (!login.value || loginBusy.value) return
  const code = loginCode.value.trim()
  if (!code) {
    loginError.value = 'Paste the code#state value first.'
    return
  }
  loginBusy.value = true
  loginError.value = null
  try {
    await completeClaudeLogin(login.value.login_id, code)
    login.value = null
    loginCode.value = ''
    await refresh(true)
  } catch (err) {
    loginError.value = err instanceof Error ? err.message : 'request failed'
  } finally {
    loginBusy.value = false
  }
}

function cancelLogin() {
  login.value = null
  loginCode.value = ''
  loginError.value = null
}

onMounted(() => {
  refresh()
  timer = window.setInterval(() => refresh(), REFRESH_MS)
})

onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
})

const STATUS_DOT: Record<string, string> = {
  active: '#22C55E',
  standby: '#D1D5DB',
  benched: '#F59E0B',
  unusable: '#EF4444',
}

function barColor(pct: number | null | undefined): string {
  if (pct == null) return '#D1D5DB'
  if (pct >= 90) return '#EF4444'
  if (pct >= 70) return '#F59E0B'
  return '#111827'
}

function barWidth(pct: number | null | undefined): string {
  if (pct == null) return '0%'
  return `${Math.min(Math.max(pct, 0), 100)}%`
}

function formatUtilization(pct: number | null | undefined): string {
  if (pct == null) return '—'
  return `${Math.round(pct)}%`
}

function formatReset(iso: string | null | undefined, withDate: boolean): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (!withDate) return `resets ${hhmm}`
  return `resets ${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(2, '0')} ${hhmm}`
}

function planLabel(tier: string | null | undefined, planType: string | null | undefined): string {
  const raw = tier ?? planType
  if (!raw) return ''
  return raw
    .replace(/^default_/, '')
    .split('_')
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ')
}
</script>

<template>
  <div class="border border-[#E5E7EB] rounded-lg p-4 shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
    <div class="flex items-center justify-between mb-3">
      <div class="text-sm font-medium text-[#111827]">Claude Accounts</div>
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="flex items-center gap-1 text-[11px] text-[#6B7280] border border-[#E5E7EB] rounded px-2 py-1 hover:text-[#111827] hover:border-[#D1D5DB] disabled:opacity-50"
          :disabled="loginBusy || login !== null"
          title="Log in another Claude account"
          @click="startLogin"
        >
          <Plus class="h-3 w-3" />
          Add account
        </button>
        <button
          type="button"
          class="flex items-center gap-1 text-[11px] text-[#6B7280] border border-[#E5E7EB] rounded px-2 py-1 hover:text-[#111827] hover:border-[#D1D5DB] disabled:opacity-50"
          :disabled="refreshing"
          title="Refresh now (bypasses cache)"
          @click="refresh(true)"
        >
          <RefreshCw class="h-3 w-3" :class="refreshing ? 'animate-spin' : ''" />
          Refresh
        </button>
      </div>
    </div>

    <div
      v-if="login"
      class="mb-4 border border-[#E5E7EB] rounded p-3 space-y-2"
    >
      <div class="text-xs text-[#111827] font-medium">Add a Claude account</div>
      <ol class="text-xs text-[#6B7280] list-decimal ml-4 space-y-1">
        <li>
          <a
            :href="login.authorization_url"
            target="_blank"
            rel="noopener"
            class="text-[#111827] underline underline-offset-2"
          >Open the Claude authorization page</a>
          and approve access.
        </li>
        <li>Paste the <span class="font-mono">code#state</span> value shown on the callback page.</li>
      </ol>
      <div class="flex items-center gap-2">
        <input
          v-model="loginCode"
          type="text"
          placeholder="code#state"
          class="flex-1 text-xs font-mono border border-[#E5E7EB] rounded px-2 py-1.5 focus:outline-none focus:border-[#111827]"
          @keydown.enter="completeLogin"
        />
        <button
          type="button"
          class="text-[11px] text-white bg-[#111827] rounded px-2.5 py-1.5 disabled:opacity-50"
          :disabled="loginBusy"
          @click="completeLogin"
        >
          Complete
        </button>
        <button
          type="button"
          class="text-[11px] text-[#6B7280] border border-[#E5E7EB] rounded px-2.5 py-1.5 hover:text-[#111827]"
          :disabled="loginBusy"
          @click="cancelLogin"
        >
          Cancel
        </button>
      </div>
      <div v-if="loginError" class="text-xs text-[#EF4444]">{{ loginError }}</div>
    </div>
    <div v-else-if="loginError" class="mb-3 text-xs text-[#EF4444]">{{ loginError }}</div>

    <div v-if="actionError" class="mb-3 text-xs text-[#EF4444]">{{ actionError }}</div>

    <div v-if="loading" class="text-xs text-[#6B7280]">Loading…</div>
    <div v-else-if="!data || !data.available" class="text-xs text-[#6B7280]">
      claude-code-proxy unavailable{{ data?.error ? ` — ${data.error}` : '' }}
    </div>
    <div v-else-if="data.accounts.length === 0" class="text-xs text-[#6B7280]">
      No Claude accounts in the proxy token store. Use “Add account” or run
      <span class="font-mono">proxy claude-code login</span>.
    </div>

    <div v-else class="space-y-4">
      <div
        v-for="acct in data.accounts"
        :key="acct.id"
        class="flex flex-col gap-2"
      >
        <div class="flex items-center gap-2 min-w-0">
          <span
            class="inline-block h-2 w-2 rounded-full shrink-0"
            :style="{ backgroundColor: STATUS_DOT[acct.status] ?? '#D1D5DB' }"
          />
          <span class="text-sm font-medium text-[#111827] truncate" :title="`token ${acct.id}`">
            {{ acct.account?.email ?? acct.id }}
          </span>
          <span
            v-if="planLabel(acct.account?.rate_limit_tier, acct.account?.plan_type)"
            class="text-[10px] text-[#6B7280] border border-[#E5E7EB] rounded px-1.5 py-0.5 shrink-0"
          >
            {{ planLabel(acct.account?.rate_limit_tier, acct.account?.plan_type) }}
          </span>
          <span class="text-[10px] text-[#6B7280] shrink-0 ml-auto uppercase tracking-wide">
            {{ acct.status }}
          </span>
          <button
            v-if="acct.priority > 0"
            type="button"
            class="text-[10px] text-[#6B7280] border border-[#E5E7EB] rounded px-1.5 py-0.5 shrink-0 hover:text-[#111827] hover:border-[#D1D5DB] disabled:opacity-50"
            :disabled="actionBusy"
            title="Make this the highest-priority account"
            @click="promote(acct)"
          >
            Promote
          </button>
          <button
            type="button"
            class="text-[10px] text-[#6B7280] border border-[#E5E7EB] rounded px-1.5 py-0.5 shrink-0 hover:text-[#EF4444] hover:border-[#EF4444] disabled:opacity-50"
            :disabled="actionBusy"
            title="Remove this account from the token store"
            @click="remove(acct)"
          >
            Remove
          </button>
        </div>

        <div v-if="acct.usage" class="grid grid-cols-2 gap-4">
          <div v-for="win in [
            { label: '5h', data: acct.usage.five_hour, withDate: false },
            { label: 'Week', data: acct.usage.seven_day, withDate: true },
          ]" :key="win.label">
            <div class="flex items-baseline justify-between mb-1">
              <span class="text-[10px] text-[#6B7280] uppercase tracking-wide">{{ win.label }}</span>
              <span class="text-xs text-[#111827] tabular-nums font-medium">
                {{ formatUtilization(win.data?.utilization) }}
              </span>
            </div>
            <div class="h-1.5 rounded-full bg-[#F3F4F6] overflow-hidden">
              <div
                class="h-full rounded-full"
                :style="{
                  width: barWidth(win.data?.utilization),
                  backgroundColor: barColor(win.data?.utilization),
                }"
              />
            </div>
            <div class="text-[10px] text-[#6B7280] mt-1 tabular-nums">
              {{ formatReset(win.data?.resets_at, win.withDate) }}
            </div>
          </div>
        </div>

        <div
          v-if="acct.error"
          class="text-xs"
          :class="acct.usage ? 'text-[#6B7280]' : 'text-[#EF4444]'"
        >
          {{ acct.stale ? 'stale — ' : '' }}{{ acct.error }}
        </div>
      </div>

      <div v-if="data.models.length" class="pt-3 border-t border-[#E5E7EB]">
        <div class="text-[10px] text-[#6B7280] uppercase tracking-wide mb-2">Models</div>
        <div class="flex flex-wrap gap-1.5">
          <span
            v-for="model in data.models"
            :key="model.id"
            class="text-[11px] text-[#111827] border border-[#E5E7EB] rounded px-1.5 py-0.5 font-mono"
            :title="model.display_name ?? model.id"
          >
            {{ model.id }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
