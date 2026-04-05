<script setup lang="ts">
import { ref } from 'vue'
import { useDashboardStore, type DateRange } from '@/stores/dashboard'

const store = useDashboardStore()

const ranges: { label: string; value: DateRange }[] = [
  { label: 'Today', value: 'today' },
  { label: '7D', value: '7d' },
  { label: '30D', value: '30d' },
]

// Default month input to current month
const now = new Date()
const monthInput = ref(store.selectedMonth || `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`)

function onMonthChange(e: Event) {
  const val = (e.target as HTMLInputElement).value
  monthInput.value = val
  store.setMonth(val)
}
</script>

<template>
  <div class="flex items-center gap-1">
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
    <div class="relative ml-1">
      <input
        type="month"
        :value="monthInput"
        class="px-2 py-1 text-sm rounded border transition-colors cursor-pointer appearance-none"
        :class="store.range === 'month'
          ? 'border-[#111827] bg-[#111827] text-white'
          : 'border-[#E5E7EB] text-[#6B7280] hover:bg-[#F3F4F6]'"
        @change="onMonthChange"
      />
    </div>
  </div>
</template>
