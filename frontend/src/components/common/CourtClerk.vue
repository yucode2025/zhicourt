<script setup lang="ts">
import { computed } from 'vue'

type ClerkVariant = 'idle' | 'greet' | 'wander' | 'computer' | 'sleepy' | 'ball'

const props = withDefaults(
  defineProps<{
    variant?: ClerkVariant
    size?: number
    caption?: string
  }>(),
  { variant: 'idle', size: 96, caption: '' },
)

const symbol = computed(() => ({
  idle: '⚖',
  greet: '◇',
  wander: '?',
  computer: '⌘',
  sleepy: '…',
  ball: '!',
})[props.variant])
</script>

<template>
  <figure class="court-clerk" :style="{ width: size + 'px' }" aria-hidden="true">
    <div class="clerk-mark" :style="{ width: size + 'px', height: size + 'px' }">
      <span :style="{ fontSize: Math.round(size * 0.42) + 'px' }">{{ symbol }}</span>
    </div>
    <figcaption v-if="caption">{{ caption }}</figcaption>
  </figure>
</template>

<style scoped>
.court-clerk {
  margin: 0;
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}
.clerk-mark {
  display: grid;
  place-items: center;
  border: 1px solid var(--c-judge-line);
  border-radius: 28%;
  background: linear-gradient(145deg, var(--c-judge-bg), #fff);
  color: var(--c-judge);
  box-shadow: inset 0 0 0 6px rgb(255 255 255 / 55%), var(--shadow-card);
  user-select: none;
}
.clerk-mark span {
  font-family: Georgia, 'Times New Roman', serif;
  font-weight: 700;
  line-height: 1;
}
.court-clerk figcaption {
  max-width: 220px;
  color: var(--c-ink-3);
  font-size: 12px;
  line-height: 1.5;
  text-align: center;
}
</style>
