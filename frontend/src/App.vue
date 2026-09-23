<script setup lang="ts">
import { NConfigProvider, NDialogProvider, NMessageProvider, zhCN, dateZhCN } from 'naive-ui'
import AppHeader from '@/components/common/AppHeader.vue'
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="{ common: { primaryColor: '#056de8' } }">
    <NMessageProvider placement="top">
      <NDialogProvider>
        <a class="skip-link" href="#main-content">跳到主要内容</a>
        <AppHeader />
        <div id="main-content">
          <RouterView />
        </div>
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
