<script setup lang="ts">
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { init, use, type ECharts, type EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

use([LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{
  option: EChartsCoreOption
  height?: number
}>()

const el = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let observer: ResizeObserver | null = null

onMounted(() => {
  if (!el.value) return
  chart = init(el.value)
  chart.setOption(props.option)
  if ('ResizeObserver' in window) {
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(el.value)
  }
})
watch(() => props.option, (option) => chart?.setOption(option, true), { deep: true })
onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div ref="el" :style="{ height: (height ?? 260) + 'px', width: '100%' }" />
</template>
