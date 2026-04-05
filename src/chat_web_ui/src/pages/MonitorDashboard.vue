<script setup lang="ts">
import { onMounted } from 'vue'
import { useDashboardStore } from '@/stores/dashboard'
import MonitorTabs from '@/components/dashboard/MonitorTabs.vue'
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
  <div>
    <MonitorTabs />
    <div class="space-y-6">
      <TimeRangeSelector />
      <SummaryCards />
      <DailyCostChart />
      <SessionsTable />
    </div>
  </div>
</template>
