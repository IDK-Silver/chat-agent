<script setup lang="ts">
import { useLiveStore } from '@/stores/live'
import { useWebSocketStore } from '@/stores/websocket'
import TokenBar from './TokenBar.vue'

const live = useLiveStore()
const ws = useWebSocketStore()
</script>

<template>
  <header class="flex items-center justify-end gap-4 px-6 py-3 border-b border-[#E5E7EB]">
    <TokenBar
      v-if="live.active"
      :current="live.promptTokens"
      :soft-limit="live.softLimit"
      :hard-limit="live.hardLimit"
    />
    <div class="flex items-center gap-1.5">
      <span
        class="w-2 h-2 rounded-full"
        :class="ws.connected ? 'bg-[#22C55E]' : 'bg-[#D1D5DB]'"
      />
      <span class="text-xs text-[#6B7280]">{{ ws.connected ? 'Live' : 'Offline' }}</span>
    </div>
  </header>
</template>
