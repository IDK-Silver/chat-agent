<script setup lang="ts">
import { onMounted } from 'vue'
import { useDashboardStore } from '@/stores/dashboard'
import TimeRangeSelector from '@/components/dashboard/TimeRangeSelector.vue'
import SummaryCards from '@/components/dashboard/SummaryCards.vue'
import DailyCostChart from '@/components/dashboard/DailyCostChart.vue'
import SessionsTable from '@/components/dashboard/SessionsTable.vue'

const store = useDashboardStore()

onMounted(() => {
  store.refresh()
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <TimeRangeSelector />
    </div>
    <SummaryCards />
    <DailyCostChart />
    <SessionsTable />
  </div>
</template>
