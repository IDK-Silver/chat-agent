<script setup lang="ts">
import { computed } from 'vue'
import { useDashboardStore } from '@/stores/dashboard'
import { formatCostShort, formatPercent } from '@/lib/format'
import { Card, CardContent } from '@/components/ui/card'

const store = useDashboardStore()

const cards = computed(() => {
  const s = store.summary as Record<string, number> | null
  if (!s) return []
  return [
    { label: 'Total Cost', value: formatCostShort(s.total_cost) },
    { label: 'Turns', value: String(s.total_turns ?? 0) },
    { label: 'Sessions', value: String(s.total_sessions ?? 0) },
    { label: 'Cache Hit', value: formatPercent(s.cache_hit_rate) },
  ]
})
</script>

<template>
  <div class="grid grid-cols-4 gap-4">
    <Card v-for="c in cards" :key="c.label" class="border-[#E5E7EB] shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
      <CardContent class="pt-4 pb-4">
        <div class="text-2xl font-semibold text-[#111827] tabular-nums">{{ c.value }}</div>
        <div class="text-xs text-[#6B7280] mt-1">{{ c.label }}</div>
      </CardContent>
    </Card>
  </div>
</template>
