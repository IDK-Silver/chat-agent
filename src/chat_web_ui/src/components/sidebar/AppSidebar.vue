<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()

const monitorItems = [
  { name: 'Overview', path: '/monitor', exact: true },
  { name: 'Requests', path: '/monitor/requests', exact: true },
]

function isMonitorActive(item: { path: string; exact: boolean }): boolean {
  if (item.exact) return route.path === item.path
  return route.path.startsWith(item.path)
}

function isMonitorSection(): boolean {
  return route.path.startsWith('/monitor')
}
</script>

<template>
  <aside class="w-[200px] shrink-0 border-r border-[#E5E7EB] flex flex-col">
    <div class="px-5 py-6">
      <span class="text-lg font-semibold text-[#111827] tracking-tight">Lincy</span>
    </div>
    <nav class="flex-1 px-3 space-y-1">
      <!-- Monitor -->
      <router-link
        to="/monitor"
        class="flex items-center px-3 py-2 rounded text-sm transition-colors"
        :class="isMonitorSection()
          ? 'text-[#111827] font-semibold'
          : 'text-[#6B7280] hover:text-[#111827]'"
      >
        Monitor
      </router-link>
      <!-- Sub items, indented -->
      <div class="pl-4 space-y-0.5">
        <router-link
          v-for="item in monitorItems"
          :key="item.path"
          :to="item.path"
          class="flex items-center px-3 py-1 rounded text-[13px] transition-colors"
          :class="isMonitorActive(item)
            ? 'text-[#111827] font-medium bg-[#F3F4F6]'
            : 'text-[#9CA3AF] hover:text-[#6B7280] hover:bg-[#F9FAFB]'"
        >
          {{ item.name }}
        </router-link>
      </div>

      <!-- Chat (disabled) -->
      <div class="flex items-center gap-2 px-3 py-2 text-sm text-[#D1D5DB] cursor-default mt-2">
        Chat
        <span class="text-[10px]">coming soon</span>
      </div>

      <!-- Settings (disabled) -->
      <div class="flex items-center px-3 py-2 text-sm text-[#D1D5DB] cursor-default">
        Settings
      </div>
    </nav>
  </aside>
</template>
