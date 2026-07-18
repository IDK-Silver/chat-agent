<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { ChevronRight, Plus, RefreshCw } from 'lucide-vue-next'
import ProxyAccountRow from '@/components/proxy/ProxyAccountRow.vue'
import {
  beginCodexLogin,
  completeCodexLogin,
  fetchCodexAccounts,
  fetchCodexLoginStatus,
  promoteCodexAccount,
  removeCodexAccount,
  type CodexAccount,
  type CodexAccountsResponse,
  type CodexLoginBegin,
} from '@/api/client'

const REFRESH_MS = 180_000
const LOGIN_POLL_MS = 2_000

const data = ref<CodexAccountsResponse | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const actionBusy = ref(false)
const actionError = ref<string | null>(null)
let timer: number | undefined

// Model ids are reference info only; keep them collapsed by default (always
// empty today since codex-proxy does not expose a model list yet).
const modelsOpen = ref(false)

const login = ref<CodexLoginBegin | null>(null)
const loginValue = ref('')
const loginBusy = ref(false)
const loginError = ref<string | null>(null)
let loginTimer: number | undefined

async function refresh(force = false) {
  if (refreshing.value) return
  refreshing.value = true
  try {
    data.value = await fetchCodexAccounts(force)
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

function promote(acct: CodexAccount) {
  runAction(() => promoteCodexAccount(acct.id))
}

function remove(acct: CodexAccount) {
  const label = acct.account?.email ?? acct.id
  if (!window.confirm(`Remove ${label} from the proxy token store?`)) return
  runAction(() => removeCodexAccount(acct.id))
}

function stopLoginPoll() {
  if (loginTimer !== undefined) {
    window.clearInterval(loginTimer)
    loginTimer = undefined
  }
}

function startLoginPoll(loginId: string) {
  stopLoginPoll()
  loginTimer = window.setInterval(() => pollLogin(loginId), LOGIN_POLL_MS)
}

async function pollLogin(loginId: string) {
  // Ignore stale ticks from a login that was already cancelled/replaced.
  if (!login.value || login.value.login_id !== loginId) return
  try {
    const status = await fetchCodexLoginStatus(loginId)
    if (status.status === 'completed') {
      stopLoginPoll()
      login.value = null
      loginValue.value = ''
      await refresh(true)
    } else if (status.status === 'expired') {
      stopLoginPoll()
      login.value = null
      loginError.value = 'Login expired. Click Add account to try again.'
    }
  } catch {
    // Transient poll failures are ignored; keep polling until completed/expired.
  }
}

async function startLogin() {
  if (loginBusy.value) return
  loginBusy.value = true
  loginError.value = null
  try {
    login.value = await beginCodexLogin()
    loginValue.value = ''
    startLoginPoll(login.value.login_id)
  } catch (err) {
    loginError.value = err instanceof Error ? err.message : 'request failed'
  } finally {
    loginBusy.value = false
  }
}

async function completeLogin() {
  if (!login.value || loginBusy.value) return
  const value = loginValue.value.trim()
  if (!value) {
    loginError.value = 'Paste the callback URL first.'
    return
  }
  loginBusy.value = true
  loginError.value = null
  try {
    await completeCodexLogin(login.value.login_id, value)
    stopLoginPoll()
    login.value = null
    loginValue.value = ''
    await refresh(true)
  } catch (err) {
    loginError.value = err instanceof Error ? err.message : 'request failed'
  } finally {
    loginBusy.value = false
  }
}

function cancelLogin() {
  stopLoginPoll()
  login.value = null
  loginValue.value = ''
  loginError.value = null
}

onMounted(() => {
  refresh()
  timer = window.setInterval(() => refresh(), REFRESH_MS)
})

onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
  stopLoginPoll()
})

function planLabel(planType: string | null | undefined): string {
  if (!planType) return ''
  return planType
    .split('_')
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(' ')
}

// codex_auth accounts come from the official Codex CLI's ~/.codex/auth.json
// (not this proxy's own token store), so they can't be promoted or removed
// here; the plan chip gets a small suffix to explain why.
function planChip(acct: CodexAccount): string {
  const base = planLabel(acct.account?.plan_type)
  if (acct.source !== 'codex_auth') return base
  return base ? `${base} · codex cli` : 'codex cli'
}

interface UsageRow {
  key: string
  label: string
  utilization: number | null | undefined
  resetsAt: string | null | undefined
  withDate: boolean
}

function usageRows(acct: CodexAccount): UsageRow[] {
  const windows = acct.usage?.windows ?? []
  return windows.map((w, i) => ({
    key: `${i}:${w.label}`,
    label: w.label,
    utilization: w.utilization,
    resetsAt: w.resets_at,
    withDate: !/^\d+h$/i.test(w.label),
  }))
}
</script>

<template>
  <div class="border border-[#E5E7EB] rounded-lg p-4 shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
    <div class="flex items-center justify-between mb-3">
      <div class="text-sm font-medium text-[#111827]">Codex Accounts</div>
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="flex items-center gap-1 text-[11px] text-[#6B7280] border border-[#E5E7EB] rounded px-2 py-1 hover:text-[#111827] hover:border-[#D1D5DB] disabled:opacity-50"
          :disabled="loginBusy || login !== null"
          title="Log in another Codex account"
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
      <div class="text-xs text-[#111827] font-medium">Add a Codex account</div>
      <ol class="text-xs text-[#6B7280] list-decimal ml-4 space-y-1">
        <li>
          <a
            :href="login.authorization_url"
            target="_blank"
            rel="noopener"
            class="text-[#111827] underline underline-offset-2"
          >Open the ChatGPT authorization page</a>
          and approve access.
        </li>
        <li>The proxy listens on localhost:1455 and completes the login automatically once approved — this panel closes on its own when that happens.</li>
      </ol>
      <div v-if="login.listener_error" class="text-[11px] text-[#6B7280]">
        automatic completion unavailable — paste the callback URL manually
      </div>
      <div class="flex items-center gap-2">
        <input
          v-model="loginValue"
          type="text"
          placeholder="paste callback URL if it did not complete"
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
      codex-proxy unavailable{{ data?.error ? ` — ${data.error}` : '' }}
    </div>
    <div v-else-if="data.accounts.length === 0" class="text-xs text-[#6B7280]">
      No Codex accounts in the proxy token store. Use "Add account" or run
      <span class="font-mono">proxy codex login</span>.
    </div>

    <div v-else>
      <div class="divide-y divide-[#F3F4F6]">
        <ProxyAccountRow
          v-for="acct in data.accounts"
          :key="acct.id"
          :name="acct.account?.email ?? acct.id"
          :title="`token ${acct.id} — ${acct.status}`"
          :status="acct.status"
          :plan="planChip(acct)"
          :rows="usageRows(acct)"
          :error="acct.error"
          :stale="acct.stale"
          :can-promote="acct.priority > 0 && acct.source !== 'codex_auth'"
          :can-remove="acct.source !== 'codex_auth'"
          :busy="actionBusy"
          @promote="promote(acct)"
          @remove="remove(acct)"
        />
      </div>

      <div v-if="data.models.length" class="mt-3 pt-3 border-t border-[#E5E7EB]">
        <button
          type="button"
          class="flex items-center gap-1 text-[10px] text-[#6B7280] uppercase tracking-wide hover:text-[#111827]"
          @click="modelsOpen = !modelsOpen"
        >
          <ChevronRight class="h-3 w-3 transition-transform" :class="modelsOpen ? 'rotate-90' : ''" />
          Models ({{ data.models.length }})
        </button>
        <div
          v-if="modelsOpen"
          class="mt-1 text-[11px] font-mono text-[#6B7280] leading-relaxed wrap-break-word"
        >
          {{ data.models.map((m) => m.id).join(' · ') }}
        </div>
      </div>
    </div>
  </div>
</template>
