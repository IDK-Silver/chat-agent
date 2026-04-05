<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fetchSessionDetail } from '@/api/client'
import { useWebSocketStore } from '@/stores/websocket'
import { formatCostShort, formatCost, formatPercent, formatTokens, formatLatency } from '@/lib/format'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'

const route = useRoute()
const router = useRouter()
const wsStore = useWebSocketStore()

const detail = ref<Record<string, unknown> | null>(null)
const expandedTurns = ref<Set<string>>(new Set())

async function load() {
  const id = route.params.id as string
  detail.value = await fetchSessionDetail(id)
}

function toggleTurn(turnId: string) {
  if (expandedTurns.value.has(turnId)) {
    expandedTurns.value.delete(turnId)
  } else {
    expandedTurns.value.add(turnId)
  }
}

function cacheRate(cr: number, cw: number): number | null {
  return (cr + cw) > 0 ? cr / (cr + cw) : null
}

onMounted(() => {
  load()
  wsStore.onMessage((msg) => {
    if (msg.type === 'session_updated' && msg.session_id === route.params.id) {
      load()
    }
  })
})

watch(() => route.params.id, load)
</script>

<template>
  <div v-if="detail" class="space-y-6">
    <!-- Header -->
    <div class="flex items-center gap-4">
      <button
        class="text-sm text-[#6B7280] hover:text-[#111827] transition-colors"
        @click="router.push('/monitor')"
      >
        &larr; Back
      </button>
      <span class="text-sm text-[#111827] font-medium">
        Session {{ (detail.meta as Record<string, unknown>)?.created_at }}
      </span>
      <Badge
        variant="secondary"
        class="text-[10px] px-1.5"
        :class="(detail.meta as Record<string, unknown>)?.status === 'active' ? 'bg-[#ECFDF5] text-[#065F46]' : ''"
      >
        {{ (detail.meta as Record<string, unknown>)?.status }}
      </Badge>
    </div>

    <!-- Summary cards -->
    <div class="grid grid-cols-4 gap-4">
      <Card class="border-[#E5E7EB] shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
        <CardContent class="pt-4 pb-4">
          <div class="text-2xl font-semibold text-[#111827] tabular-nums">
            {{ formatCostShort(((detail.summary as Record<string, unknown>)?.total_cost as number) ?? null) }}
          </div>
          <div class="text-xs text-[#6B7280] mt-1">Total Cost</div>
        </CardContent>
      </Card>
      <Card class="border-[#E5E7EB] shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
        <CardContent class="pt-4 pb-4">
          <div class="text-2xl font-semibold text-[#111827] tabular-nums">
            {{ (detail.summary as Record<string, unknown>)?.turn_count }}
          </div>
          <div class="text-xs text-[#6B7280] mt-1">Turns</div>
        </CardContent>
      </Card>
      <Card class="border-[#E5E7EB] shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
        <CardContent class="pt-4 pb-4">
          <div class="text-2xl font-semibold text-[#111827] tabular-nums">
            {{ formatPercent(((detail.summary as Record<string, unknown>)?.cache_hit_rate as number) ?? null) }}
          </div>
          <div class="text-xs text-[#6B7280] mt-1">Cache Hit Rate</div>
        </CardContent>
      </Card>
      <Card class="border-[#E5E7EB] shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
        <CardContent class="pt-4 pb-4">
          <div class="text-2xl font-semibold text-[#111827] tabular-nums">
            {{ formatTokens(((detail.summary as Record<string, unknown>)?.peak_prompt_tokens as number) ?? 0) }}
          </div>
          <div class="text-xs text-[#6B7280] mt-1">Peak Tokens</div>
        </CardContent>
      </Card>
    </div>

    <!-- Turn timeline -->
    <div class="border border-[#E5E7EB] rounded-lg shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
      <div class="px-4 py-3 text-sm font-medium text-[#111827]">Turn Timeline</div>
      <div class="divide-y divide-[#E5E7EB]">
        <div
          v-for="turn in (detail.turns as Record<string, unknown>[])"
          :key="(turn.turn_id as string)"
          class="px-4 py-3"
        >
          <!-- Turn header -->
          <button
            class="w-full flex items-center gap-3 text-left"
            @click="toggleTurn(turn.turn_id as string)"
          >
            <span class="text-xs text-[#6B7280] tabular-nums w-16">
              {{ (turn.turn_id as string).replace('turn_', '#') }}
            </span>
            <span class="text-xs text-[#6B7280] tabular-nums w-12">
              {{ new Date(turn.ts_started as string).toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit', hour12: false }) }}
            </span>
            <span class="text-xs text-[#111827] w-16">{{ turn.channel }}</span>
            <span class="text-xs text-[#6B7280] w-16 truncate">{{ turn.sender }}</span>
            <span class="text-xs text-[#6B7280] tabular-nums w-20">
              {{ turn.llm_rounds }} rounds
            </span>
            <span class="text-xs text-[#6B7280] tabular-nums w-16">
              {{ formatPercent(cacheRate(turn.cache_read_tokens as number, turn.cache_write_tokens as number)) }}
            </span>
            <span class="text-xs text-[#111827] tabular-nums ml-auto">
              {{ formatCost(turn.total_cost as number) }}
            </span>
            <span class="text-xs text-[#D1D5DB]">
              {{ expandedTurns.has(turn.turn_id as string) ? '&#9660;' : '&#9654;' }}
            </span>
          </button>

          <!-- Expanded responses -->
          <div
            v-if="expandedTurns.has(turn.turn_id as string)"
            class="mt-2 ml-8 space-y-1"
          >
            <div
              v-for="(resp, idx) in (turn.responses as Record<string, unknown>[])"
              :key="idx"
              class="flex items-center gap-3 text-xs text-[#6B7280] tabular-nums py-1 border-l-2 border-[#E5E7EB] pl-3"
            >
              <span class="w-8">r{{ resp.round }}</span>
              <span class="w-16 truncate">{{ resp.model }}</span>
              <span class="w-14">{{ formatTokens((resp.prompt_tokens as number) || 0) }}</span>
              <span class="w-12">
                {{ formatPercent(cacheRate(resp.cache_read_tokens as number, resp.cache_write_tokens as number)) }}
              </span>
              <span class="w-14">{{ formatLatency((resp.latency_ms as number) || 0) }}</span>
              <span class="ml-auto text-[#111827]">{{ formatCost(resp.cost as number) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div v-else class="text-[#6B7280] text-sm">Loading...</div>
</template>
