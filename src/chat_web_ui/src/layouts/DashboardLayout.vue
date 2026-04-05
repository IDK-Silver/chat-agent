<script setup lang="ts">
import AppSidebar from '@/components/sidebar/AppSidebar.vue'
import TopBar from '@/components/topbar/TopBar.vue'
</script>

<template>
  <div class="flex h-screen bg-white">
    <AppSidebar />
    <div class="flex flex-col flex-1 min-w-0">
      <TopBar />
      <main class="flex-1 overflow-y-auto p-6">
        <router-view />
      </main>
    </div>
  </div>
</template>
