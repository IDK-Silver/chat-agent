<script setup lang="ts">
import { useDashboardStore, type DateRange } from '@/stores/dashboard'

const store = useDashboardStore()

const ranges: { label: string; value: DateRange }[] = [
  { label: 'Today', value: 'today' },
  { label: '7D', value: '7d' },
  { label: '30D', value: '30d' },
]
</script>

<template>
  <div class="flex gap-1">
    <button
      v-for="r in ranges"
      :key="r.value"
      class="px-3 py-1 text-sm rounded transition-colors"
      :class="store.range === r.value
        ? 'bg-[#111827] text-white'
        : 'text-[#6B7280] hover:bg-[#F3F4F6]'"
      @click="store.setRange(r.value)"
    >
      {{ r.label }}
    </button>
  </div>
</template>
