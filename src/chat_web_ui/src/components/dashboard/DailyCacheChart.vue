<script setup lang="ts">
import { computed } from 'vue'
import { Line } from 'vue-chartjs'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Tooltip,
  Filler,
} from 'chart.js'
import { useDashboardStore } from '@/stores/dashboard'

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Tooltip, Filler)

const store = useDashboardStore()

const chartData = computed(() => {
  const costs = (store.summary as Record<string, unknown>)?.daily_costs as
    { date: string; cache_read: number; cache_write: number }[] || []
  return {
    labels: costs.map((d) => d.date.slice(5)),
    datasets: [
      {
        data: costs.map((d) => {
          const total = d.cache_read + d.cache_write
          return total > 0 ? (d.cache_read / total) * 100 : null
        }),
        borderColor: '#111827',
        backgroundColor: 'rgba(17, 24, 39, 0.04)',
        borderWidth: 1.5,
        pointRadius: 3,
        pointBackgroundColor: '#111827',
        tension: 0.3,
        fill: true,
      },
    ],
  }
})

const chartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: {
    legend: { display: false },
    tooltip: {
      callbacks: {
        label: (ctx: { parsed: { y: number | null } }) => `${(ctx.parsed.y ?? 0).toFixed(1)}%`,
      },
    },
  },
  scales: {
    x: {
      grid: { display: false },
      ticks: { color: '#6B7280', font: { size: 11 } },
    },
    y: {
      min: 0,
      max: 100,
      grid: { color: '#F3F4F6' },
      ticks: {
        color: '#6B7280',
        font: { size: 11 },
        callback: (v: number | string) => `${v}%`,
      },
    },
  },
}
</script>

<template>
  <div class="border border-[#E5E7EB] rounded-lg p-4 shadow-[0_1px_2px_rgba(0,0,0,0.04)]">
    <div class="text-sm font-medium text-[#111827] mb-3">Daily Cache Hit Rate</div>
    <div class="h-48">
      <Line :data="chartData" :options="chartOptions" />
    </div>
  </div>
</template>
