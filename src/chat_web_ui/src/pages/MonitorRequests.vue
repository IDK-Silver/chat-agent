<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { fetchAllRequests } from '@/api/client'
import { useDashboardStore } from '@/stores/dashboard'
import { useWebSocketStore } from '@/stores/websocket'
import { formatCost, formatCostShort, formatPercent, formatTokens, formatLatency } from '@/lib/format'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import MonitorTabs from '@/components/dashboard/MonitorTabs.vue'
import TimeRangeSelector from '@/components/dashboard/TimeRangeSelector.vue'

const dashStore = useDashboardStore()
const wsStore = useWebSocketStore()

const requests = ref<Record<string, unknown>[]>([])
const total = ref(0)
const loading = ref(false)

function cacheRate(cr: number, cw: number): number | null {
  return (cr + cw) > 0 ? cr / (cr + cw) : null
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

interface SessionGroup {
  session_id: string
  session_label: string
  requests: Record<string, unknown>[]
  total_cost: number
}

const grouped = computed(() => {
  const groups: SessionGroup[] = []
  let current: SessionGroup | null = null
  for (const r of requests.value) {
    const sid = r.session_id as string
    if (!current || current.session_id !== sid) {
      current = { session_id: sid, session_label: r.session_label as string, requests: [], total_cost: 0 }
      groups.push(current)
    }
    current.requests.push(r)
    if (typeof r.cost === 'number') current.total_cost += r.cost
  }
  return groups
})

function _localDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
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
    const data = await fetchAllRequests(from, to, 500)
    requests.value = data.requests || []
    total.value = data.total || 0
  } finally {
    loading.value = false
  }
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
</script>

<template>
  <div>
    <MonitorTabs />
    <div class="space-y-6">
    <div class="flex items-center justify-between">
      <TimeRangeSelector />
      <span class="text-xs text-[#6B7280] tabular-nums">{{ total }} requests</span>
    </div>

    <div
      v-for="group in grouped"
      :key="group.session_id"
      class="border border-[#E5E7EB] rounded-lg shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
    >
      <!-- Session header -->
      <div class="flex items-center justify-between px-4 py-2.5 border-b border-[#F3F4F6]">
        <router-link
          :to="`/monitor/${group.session_id}`"
          class="text-sm font-medium text-[#111827] hover:underline"
        >
          Session {{ group.session_label }}
        </router-link>
        <div class="flex items-center gap-4 text-xs text-[#6B7280] tabular-nums">
          <span>{{ group.requests.length }} requests</span>
          <span>{{ formatCostShort(group.total_cost) }}</span>
        </div>
      </div>

      <Table>
        <TableHeader>
          <TableRow class="text-xs text-[#6B7280]">
            <TableHead class="w-20">Time</TableHead>
            <TableHead class="w-12">Round</TableHead>
            <TableHead class="w-36">Model</TableHead>
            <TableHead class="w-16 text-right">Prompt</TableHead>
            <TableHead class="w-16 text-right">Output</TableHead>
            <TableHead class="w-16 text-right">Cache</TableHead>
            <TableHead class="w-16 text-right">Latency</TableHead>
            <TableHead class="w-20 text-right">Cost</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow
            v-for="(r, idx) in group.requests"
            :key="idx"
            class="hover:bg-[#F9FAFB] transition-colors"
          >
            <TableCell class="text-xs text-[#6B7280] tabular-nums">
              {{ formatTime(r.ts as string) }}
            </TableCell>
            <TableCell class="text-xs text-[#6B7280] tabular-nums">
              r{{ r.round }}
            </TableCell>
            <TableCell class="text-xs text-[#111827] truncate max-w-[144px]">
              {{ r.model }}
            </TableCell>
            <TableCell class="text-xs text-right tabular-nums">
              {{ formatTokens((r.prompt_tokens as number) || 0) }}
            </TableCell>
            <TableCell class="text-xs text-right tabular-nums">
              {{ formatTokens((r.completion_tokens as number) || 0) }}
            </TableCell>
            <TableCell class="text-xs text-right tabular-nums">
              {{ formatPercent(cacheRate((r.cache_read_tokens as number) || 0, (r.cache_write_tokens as number) || 0)) }}
            </TableCell>
            <TableCell class="text-xs text-right tabular-nums">
              {{ formatLatency((r.latency_ms as number) || 0) }}
            </TableCell>
            <TableCell class="text-xs text-right tabular-nums text-[#111827]">
              {{ formatCost(r.cost as number) }}
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>
    </div>
    </div>
  </div>
</template>
