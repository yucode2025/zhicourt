<script setup lang="ts">
defineProps<{ isDemo?: boolean; label?: string }>()
</script>

<template>
  <span v-if="isDemo" class="demo-badge">◇ {{ label ?? '演示数据' }}</span>
</template>
