<script setup lang="ts">
import { NButton } from 'naive-ui'
import { computed, toRef } from 'vue'
import { useLink, type RouteLocationRaw } from 'vue-router'

defineOptions({ inheritAttrs: false })

const props = defineProps<{
  to: RouteLocationRaw
  replace?: boolean
  type?: 'default' | 'tertiary' | 'primary' | 'info' | 'success' | 'warning' | 'error'
  size?: 'tiny' | 'small' | 'medium' | 'large'
  round?: boolean
  block?: boolean
  ghost?: boolean
  quaternary?: boolean
  disabled?: boolean
}>()

const { href, navigate, isActive } = useLink({
  to: toRef(props, 'to'),
  replace: computed(() => props.replace ?? false),
})
</script>

<template>
  <NButton
    v-bind="$attrs"
    tag="a"
    :href="href"
    :type="type"
    :size="size"
    :round="round"
    :block="block"
    :ghost="ghost"
    :quaternary="quaternary"
    :disabled="disabled"
    :class="{ 'router-link-active': isActive }"
    @click="navigate"
  >
    <slot />
  </NButton>
</template>
