<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { X } from 'lucide-vue-next'
import { fetchAllRequests, fetchRequestDetail } from '@/api/client'
import { useDashboardStore } from '@/stores/dashboard'
import { useWebSocketStore } from '@/stores/websocket'
import {
  formatCacheRate,
  formatCacheWriteTokens,
  formatCost,
  formatLatency,
  formatTokens,
} from '@/lib/format'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import MonitorTabs from '@/components/dashboard/MonitorTabs.vue'
import TimeRangeSelector from '@/components/dashboard/TimeRangeSelector.vue'

const dashStore = useDashboardStore()
const wsStore = useWebSocketStore()

const requests = ref<RequestRow[]>([])
const clientLabels = ref<string[]>([])
const selectedClientLabel = ref('')
const total = ref(0)
const loading = ref(false)
const selectedRow = ref<RequestRow | null>(null)
const detail = ref<RequestDetail | null>(null)
const detailLoading = ref(false)

interface RequestRow {
  ts: string
  session_id: string
  session_label: string
  request_id: string
  turn_id: string | null
  round: number | null
  client_label: string
  provider: string | null
  model: string | null
  call_type: string
  message_count: number
  tool_count: number
  image_count: number
  has_image: boolean
  has_response_schema: boolean
  status: 'completed' | 'failed' | 'pending' | string
  usage_available: boolean
  prompt_tokens: number | null
  completion_tokens: number | null
  read_cache_rate: number | null
  cache_write_tokens: number | null
  write_cache_measurable: boolean
  latency_ms: number | null
  cost: number | null
  error: string | null
}

interface RequestDetail {
  session_id: string
  request_id: string
  ts: string
  turn_id: string | null
  round: number | null
  client_label: string
  provider: string | null
  model: string | null
  call_type: string
  temperature: number | null
  response_schema: unknown
  messages: DetailMessage[]
  tools: Record<string, unknown>[]
  response: Record<string, unknown> | null
  error?: string
}

interface DetailMessage {
  role: string
  name?: string
  tool_call_id?: string
  tool_calls?: Record<string, unknown>[]
  content: DetailPart[]
}

interface DetailPart {
  type: string
  text?: string
  media_type?: string
  width?: number
  height?: number
  data_size_bytes?: number
  thumbnail_data_url?: string
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

function formatSession(label: string): string {
  return label || '-'
}

function formatTurn(turnId: string | null): string {
  return turnId ? turnId.replace('turn_', '#') : '-'
}

function formatType(type: string): string {
  return type === 'chat_with_tools' ? 'tools' : type
}

function formatNullableTokens(tokens: number | null): string {
  return tokens == null ? '-' : formatTokens(tokens)
}

function formatNullableLatency(ms: number | null): string {
  return ms == null ? '-' : formatLatency(ms)
}

function formatStatusClass(status: string): string {
  if (status === 'completed') return 'bg-[#ECFDF5] text-[#065F46]'
  if (status === 'failed') return 'bg-[#FEF2F2] text-[#991B1B]'
  return 'bg-[#F9FAFB] text-[#6B7280]'
}

function _localDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function asJson(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

function bytesLabel(bytes: number | null | undefined): string {
  if (bytes == null) return '-'
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(1)} KB`
  return `${bytes} B`
}

const dateRange = computed(() => {
  const today = new Date()
  const to = _localDate(today)
  if (dashStore.range === 'today') return { from: to, to }
  if (dashStore.range === '7d') {
    const d = new Date(today)
    d.setDate(d.getDate() - 6)
    return { from: _localDate(d), to }
  }
  if (dashStore.range === '30d') {
    const d = new Date(today)
    d.setDate(d.getDate() - 29)
    return { from: _localDate(d), to }
  }
  return { from: dashStore.customFrom || to, to: dashStore.customTo || to }
})

async function load() {
  loading.value = true
  try {
    const { from, to } = dateRange.value
    const data = await fetchAllRequests(from, to, 500, 0, selectedClientLabel.value || undefined)
    requests.value = data.requests || []
    clientLabels.value = data.client_labels || []
    total.value = data.total || 0
    if (selectedRow.value) {
      const stillVisible = requests.value.some(
        (row) => row.session_id === selectedRow.value?.session_id
          && row.request_id === selectedRow.value?.request_id,
      )
      if (!stillVisible) closeDetail()
    }
  } finally {
    loading.value = false
  }
}

async function openDetail(row: RequestRow) {
  selectedRow.value = row
  detail.value = null
  detailLoading.value = true
  try {
    detail.value = await fetchRequestDetail(row.session_id, row.request_id)
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  selectedRow.value = null
  detail.value = null
}

onMounted(() => {
  load()
  wsStore.onMessage((msg) => {
    if (msg.type === 'session_updated' || msg.type === 'session_created') {
      load()
    }
  })
})

watch(() => dashStore.range, load)
watch(() => dashStore.customFrom, load)
watch(() => dashStore.customTo, load)
watch(selectedClientLabel, load)
</script>

<template>
  <div>
    <MonitorTabs />
    <div class="space-y-6">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div class="flex flex-col gap-3 sm:flex-row sm:items-center">
          <TimeRangeSelector />
          <label class="flex items-center gap-2 text-xs text-[#6B7280]">
            Agent
            <select
              v-model="selectedClientLabel"
              class="h-8 rounded-md border border-[#E5E7EB] bg-white px-2 text-xs text-[#111827] outline-none focus:border-[#111827]"
            >
              <option value="">All</option>
              <option
                v-for="label in clientLabels"
                :key="label"
                :value="label"
              >
                {{ label }}
              </option>
            </select>
          </label>
        </div>
        <span class="text-xs text-[#6B7280] tabular-nums lg:text-right">
          {{ loading ? 'Loading...' : `${total} requests` }}
        </span>
      </div>

      <div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
        <div class="overflow-hidden rounded-lg border border-[#E5E7EB] shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
          <div class="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow class="text-xs text-[#6B7280]">
                  <TableHead class="w-20">Time</TableHead>
                  <TableHead class="w-28">Agent</TableHead>
                  <TableHead class="w-24">Session</TableHead>
                  <TableHead class="w-16">Turn</TableHead>
                  <TableHead class="w-16">Type</TableHead>
                  <TableHead class="w-40">Model</TableHead>
                  <TableHead class="w-20 text-right">Messages</TableHead>
                  <TableHead class="w-16 text-right">Tools</TableHead>
                  <TableHead class="w-24 text-right">Tokens</TableHead>
                  <TableHead class="w-24 text-right">Cache</TableHead>
                  <TableHead class="w-20 text-right">Latency</TableHead>
                  <TableHead class="w-20 text-right">Cost</TableHead>
                  <TableHead class="w-24 text-right">Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow
                  v-for="row in requests"
                  :key="`${row.session_id}:${row.request_id}`"
                  class="cursor-pointer hover:bg-[#F9FAFB] transition-colors"
                  :class="selectedRow?.request_id === row.request_id && selectedRow?.session_id === row.session_id ? 'bg-[#F9FAFB]' : ''"
                  @click="openDetail(row)"
                >
                  <TableCell class="text-xs text-[#6B7280] tabular-nums">
                    {{ formatTime(row.ts) }}
                  </TableCell>
                  <TableCell class="text-xs text-[#111827] max-w-[112px] truncate">
                    {{ row.client_label }}
                  </TableCell>
                  <TableCell class="text-xs text-[#6B7280] tabular-nums">
                    {{ formatSession(row.session_label) }}
                  </TableCell>
                  <TableCell class="text-xs text-[#6B7280] tabular-nums">
                    {{ formatTurn(row.turn_id) }}
                  </TableCell>
                  <TableCell class="text-xs text-[#6B7280]">
                    {{ formatType(row.call_type) }}
                  </TableCell>
                  <TableCell class="text-xs text-[#111827] max-w-[160px] truncate">
                    {{ row.model || row.provider || '-' }}
                  </TableCell>
                  <TableCell class="text-xs text-right tabular-nums">
                    {{ row.message_count }}{{ row.has_image ? ` / ${row.image_count} img` : '' }}
                  </TableCell>
                  <TableCell class="text-xs text-right tabular-nums">
                    {{ row.tool_count }}
                  </TableCell>
                  <TableCell class="text-xs text-right tabular-nums">
                    {{ formatNullableTokens(row.prompt_tokens) }} / {{ formatNullableTokens(row.completion_tokens) }}
                  </TableCell>
                  <TableCell class="text-xs text-right tabular-nums">
                    <div>{{ formatCacheRate(row.read_cache_rate) }}</div>
                    <div class="text-[10px] text-[#9CA3AF]">
                      {{ formatCacheWriteTokens(row.write_cache_measurable, row.cache_write_tokens) }}
                    </div>
                  </TableCell>
                  <TableCell class="text-xs text-right tabular-nums">
                    {{ formatNullableLatency(row.latency_ms) }}
                  </TableCell>
                  <TableCell class="text-xs text-right tabular-nums text-[#111827]">
                    {{ formatCost(row.cost) }}
                  </TableCell>
                  <TableCell class="text-right">
                    <Badge
                      variant="secondary"
                      class="px-1.5 py-0 text-[10px]"
                      :class="formatStatusClass(row.status)"
                    >
                      {{ row.status }}
                    </Badge>
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </div>
        </div>

        <aside
          v-if="selectedRow"
          class="rounded-lg border border-[#E5E7EB] shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
        >
          <div class="flex items-start justify-between gap-3 border-b border-[#F3F4F6] px-4 py-3">
            <div class="min-w-0">
              <div class="truncate text-sm font-medium text-[#111827]">
                {{ selectedRow.client_label }} / {{ selectedRow.request_id }}
              </div>
              <div class="mt-1 text-xs text-[#6B7280] tabular-nums">
                {{ selectedRow.session_label }} - {{ formatTurn(selectedRow.turn_id) }} - r{{ selectedRow.round ?? '-' }}
              </div>
            </div>
            <button
              class="grid h-7 w-7 shrink-0 place-items-center rounded-md text-[#6B7280] hover:bg-[#F9FAFB] hover:text-[#111827]"
              title="Close"
              @click="closeDetail"
            >
              <X class="h-4 w-4" />
            </button>
          </div>

          <div v-if="detailLoading" class="px-4 py-6 text-sm text-[#6B7280]">
            Loading request...
          </div>
          <div v-else-if="detail?.error" class="px-4 py-6 text-sm text-[#991B1B]">
            {{ detail.error }}
          </div>
          <div v-else-if="detail" class="max-h-[calc(100vh-220px)] space-y-4 overflow-y-auto px-4 py-4">
            <section class="space-y-2">
              <div class="text-xs font-medium uppercase tracking-normal text-[#6B7280]">Request</div>
              <div class="grid grid-cols-2 gap-2 text-xs">
                <div class="text-[#6B7280]">Type</div>
                <div class="text-right text-[#111827]">{{ formatType(detail.call_type) }}</div>
                <div class="text-[#6B7280]">Model</div>
                <div class="truncate text-right text-[#111827]">{{ detail.model || detail.provider || '-' }}</div>
                <div class="text-[#6B7280]">Temperature</div>
                <div class="text-right text-[#111827] tabular-nums">{{ detail.temperature ?? '-' }}</div>
                <div class="text-[#6B7280]">Tools</div>
                <div class="text-right text-[#111827] tabular-nums">{{ detail.tools.length }}</div>
              </div>
            </section>

            <section v-if="detail.response" class="space-y-2">
              <div class="text-xs font-medium uppercase tracking-normal text-[#6B7280]">Response</div>
              <pre class="max-h-48 overflow-auto rounded-md bg-[#F9FAFB] p-3 text-xs text-[#111827]">{{ asJson(detail.response) }}</pre>
            </section>

            <section class="space-y-3">
              <div class="text-xs font-medium uppercase tracking-normal text-[#6B7280]">Messages</div>
              <div
                v-for="(message, idx) in detail.messages"
                :key="idx"
                class="rounded-md border border-[#E5E7EB]"
              >
                <div class="flex items-center justify-between border-b border-[#F3F4F6] px-3 py-2">
                  <span class="text-xs font-medium text-[#111827]">{{ message.role }}</span>
                  <span v-if="message.name || message.tool_call_id" class="truncate text-xs text-[#6B7280]">
                    {{ message.name || message.tool_call_id }}
                  </span>
                </div>
                <div class="space-y-3 px-3 py-3">
                  <div
                    v-for="(part, partIdx) in message.content"
                    :key="partIdx"
                  >
                    <pre
                      v-if="part.type === 'text'"
                      class="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[#F9FAFB] p-3 text-xs text-[#111827]"
                    >{{ part.text }}</pre>
                    <div
                      v-else-if="part.type === 'image'"
                      class="space-y-2"
                    >
                      <img
                        v-if="part.thumbnail_data_url"
                        :src="part.thumbnail_data_url"
                        class="max-h-48 rounded-md border border-[#E5E7EB] object-contain"
                        alt="request image preview"
                      >
                      <div class="text-xs text-[#6B7280] tabular-nums">
                        {{ part.media_type || 'image' }}
                        - {{ part.width || '-' }}x{{ part.height || '-' }}
                        - {{ bytesLabel(part.data_size_bytes) }}
                      </div>
                    </div>
                    <pre
                      v-else
                      class="max-h-48 overflow-auto rounded-md bg-[#F9FAFB] p-3 text-xs text-[#111827]"
                    >{{ asJson(part) }}</pre>
                  </div>
                  <pre
                    v-if="message.tool_calls?.length"
                    class="max-h-48 overflow-auto rounded-md bg-[#F9FAFB] p-3 text-xs text-[#111827]"
                  >{{ asJson(message.tool_calls) }}</pre>
                </div>
              </div>
            </section>

            <section v-if="detail.tools.length" class="space-y-2">
              <div class="text-xs font-medium uppercase tracking-normal text-[#6B7280]">Tool Definitions</div>
              <pre class="max-h-72 overflow-auto rounded-md bg-[#F9FAFB] p-3 text-xs text-[#111827]">{{ asJson(detail.tools) }}</pre>
            </section>

            <section v-if="detail.response_schema" class="space-y-2">
              <div class="text-xs font-medium uppercase tracking-normal text-[#6B7280]">Response Schema</div>
              <pre class="max-h-72 overflow-auto rounded-md bg-[#F9FAFB] p-3 text-xs text-[#111827]">{{ asJson(detail.response_schema) }}</pre>
            </section>
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>
