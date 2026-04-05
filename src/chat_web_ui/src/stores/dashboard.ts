import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { fetchDashboard, fetchSessions } from '@/api/client'

export type DateRange = 'today' | '7d' | '30d' | 'custom'

function formatDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

function getRange(range: DateRange, customFrom?: string, customTo?: string) {
  const today = new Date()
  const to = formatDate(today)
  if (range === 'today') return { from: to, to }
  if (range === '7d') {
    const d = new Date(today)
    d.setDate(d.getDate() - 6)
    return { from: formatDate(d), to }
  }
  if (range === '30d') {
    const d = new Date(today)
    d.setDate(d.getDate() - 29)
    return { from: formatDate(d), to }
  }
  return { from: customFrom || to, to: customTo || to }
}

export const useDashboardStore = defineStore('dashboard', () => {
  const range = ref<DateRange>('today')
  const customFrom = ref('')
  const customTo = ref('')
  const summary = ref<Record<string, unknown> | null>(null)
  const sessions = ref<Record<string, unknown>[]>([])
  const totalSessions = ref(0)
  const loading = ref(false)

  const dateRange = computed(() => getRange(range.value, customFrom.value, customTo.value))

  async function refresh() {
    loading.value = true
    try {
      const { from, to } = dateRange.value
      const [dashData, sessData] = await Promise.all([
        fetchDashboard(from, to),
        fetchSessions(from, to, 50),
      ])
      summary.value = dashData
      sessions.value = sessData.sessions || []
      totalSessions.value = sessData.total || 0
    } finally {
      loading.value = false
    }
  }

  function setRange(r: DateRange, from?: string, to?: string) {
    range.value = r
    if (from) customFrom.value = from
    if (to) customTo.value = to
    refresh()
  }

  return { range, customFrom, customTo, summary, sessions, totalSessions, loading, refresh, setRange }
})
