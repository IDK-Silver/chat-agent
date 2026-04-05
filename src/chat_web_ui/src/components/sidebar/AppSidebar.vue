<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()

const monitorItems = [
  { name: 'Overview', path: '/monitor', exact: true },
  { name: 'Requests', path: '/monitor/requests', exact: true },
]

const navItems = [
  { name: 'Chat', path: '/chat', enabled: false, label: 'coming soon' },
  { name: 'Settings', path: '/settings', enabled: false },
]

function isMonitorActive(item: { path: string; exact: boolean }): boolean {
  if (item.exact) return route.path === item.path
  return route.path.startsWith(item.path)
}
</script>

<template>
  <aside class="w-[200px] shrink-0 border-r border-[#E5E7EB] flex flex-col">
    <div class="px-5 py-6">
      <span class="text-lg font-semibold text-[#111827] tracking-tight">Lincy</span>
    </div>
    <nav class="flex-1 px-3">
      <!-- Monitor section -->
      <div class="mb-1">
        <div class="px-3 py-1.5 text-xs font-medium text-[#111827] uppercase tracking-wider">
          Monitor
        </div>
        <router-link
          v-for="item in monitorItems"
          :key="item.path"
          :to="item.path"
          class="flex items-center gap-2 px-3 py-1.5 rounded text-sm transition-colors"
          :class="isMonitorActive(item)
            ? 'text-[#111827] font-semibold bg-[#F9FAFB]'
            : 'text-[#6B7280] hover:text-[#111827] hover:bg-[#F9FAFB]'"
        >
          {{ item.name }}
        </router-link>
      </div>

      <!-- Other nav items -->
      <div class="mt-4">
        <template v-for="item in navItems" :key="item.path">
          <div class="flex items-center gap-2 px-3 py-1.5 text-sm text-[#D1D5DB] cursor-default">
            {{ item.name }}
            <span v-if="item.label" class="text-[10px] text-[#D1D5DB]">{{ item.label }}</span>
          </div>
        </template>
      </div>
    </nav>
  </aside>
</template>
