<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { fetchAllRequests } from '@/api/client'
import { useDashboardStore } from '@/stores/dashboard'
import { useWebSocketStore } from '@/stores/websocket'
import { formatCost, formatPercent, formatTokens, formatLatency } from '@/lib/format'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import TimeRangeSelector from '@/components/dashboard/TimeRangeSelector.vue'

const dashStore = useDashboardStore()
const wsStore = useWebSocketStore()

const requests = ref<Record<string, unknown>[]>([])
const total = ref(0)
const loading = ref(false)

function cacheRate(cr: number, cw: number): number | null {
  return (cr + cw) > 0 ? cr / (cr + cw) : null
}

const dateRange = computed(() => {
  const today = new Date()
  const to = today.toISOString().slice(0, 10)
  if (dashStore.range === 'today') return { from: to, to }
  if (dashStore.range === '7d') {
    const d = new Date(today)
    d.setDate(d.getDate() - 6)
    return { from: d.toISOString().slice(0, 10), to }
  }
  if (dashStore.range === '30d') {
    const d = new Date(today)
    d.setDate(d.getDate() - 29)
    return { from: d.toISOString().slice(0, 10), to }
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

function onRangeChange() {
  // TimeRangeSelector updates dashStore, we react
  setTimeout(load, 50)
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <TimeRangeSelector @click.capture="onRangeChange" />
      <span class="text-xs text-[#6B7280] tabular-nums">{{ total }} requests</span>
    </div>

    <div class="border border-[#E5E7EB] rounded-lg shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
      <Table>
        <TableHeader>
          <TableRow class="text-xs text-[#6B7280]">
            <TableHead class="w-24">Session</TableHead>
            <TableHead class="w-20">Turn</TableHead>
            <TableHead class="w-10">Round</TableHead>
            <TableHead class="w-32">Model</TableHead>
            <TableHead class="w-16 text-right">Prompt</TableHead>
            <TableHead class="w-16 text-right">Output</TableHead>
            <TableHead class="w-16 text-right">Cache</TableHead>
            <TableHead class="w-16 text-right">Latency</TableHead>
            <TableHead class="w-20 text-right">Cost</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow
            v-for="(r, idx) in requests"
            :key="idx"
            class="hover:bg-[#F9FAFB] transition-colors"
          >
            <TableCell class="text-xs text-[#6B7280] tabular-nums">
              <router-link
                :to="`/monitor/${r.session_id}`"
                class="hover:text-[#111827] hover:underline"
              >
                {{ r.session_label }}
              </router-link>
            </TableCell>
            <TableCell class="text-xs text-[#6B7280] tabular-nums">
              {{ ((r.turn_id as string) || '').replace('turn_', '#') }}
            </TableCell>
            <TableCell class="text-xs text-[#6B7280] tabular-nums">
              r{{ r.round }}
            </TableCell>
            <TableCell class="text-xs text-[#111827] truncate max-w-[128px]">
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
</template>
