<script setup lang="ts">
import { Chart, LineController, LineElement, PointElement, LinearScale, Tooltip, Filler, Legend, type Plugin } from 'chart.js'
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import type { KaplanMeierEstimate } from '../lib/types'

Chart.register(LineController, LineElement, PointElement, LinearScale, Tooltip, Filler, Legend)
const props = defineProps<{ estimate: KaplanMeierEstimate }>()
const el = ref<HTMLCanvasElement>()
let chart: Chart | undefined

function render() {
  if (!el.value) return
  chart?.destroy()
  const css = getComputedStyle(document.documentElement)
  const muted = css.getPropertyValue('--muted').trim()
  const grid = css.getPropertyValue('--line').trim()
  const amber = css.getPropertyValue('--amber').trim()
  const surface = css.getPropertyValue('--surface').trim()
  const survival = [{ x: 0, y: 1 }, ...props.estimate.points.map(p => ({ x: p.time_days, y: p.survival_probability, ...p }))]
  const withBand = props.estimate.points.filter(p => p.ci_95_lower !== null && p.ci_95_upper !== null)
  const lower = withBand.length === props.estimate.points.length ? [{ x: 0, y: 1 }, ...withBand.map(p => ({ x: p.time_days, y: p.ci_95_lower! }))] : []
  const upper = withBand.length === props.estimate.points.length ? [{ x: 0, y: 1 }, ...withBand.map(p => ({ x: p.time_days, y: p.ci_95_upper! }))] : []
  const censored = props.estimate.points
    .filter(p => p.n_censored > 0)
    .map(p => ({ x: p.time_days, y: p.survival_probability, ...p }))

  // Draw an explicit amber × over censor times. It cannot be mistaken for a
  // survival step or for an extra confidence-bound line.
  const censorMarks: Plugin<'line'> = {
    id: 'censorMarks',
    afterDatasetsDraw(chartInstance) {
      const { ctx } = chartInstance
      const censorPoints = chartInstance.getDatasetMeta(3).data
      ctx.save()
      for (const point of censorPoints) {
        ctx.strokeStyle = surface
        ctx.lineWidth = 5
        ctx.beginPath()
        ctx.moveTo(point.x - 5, point.y - 5); ctx.lineTo(point.x + 5, point.y + 5)
        ctx.moveTo(point.x + 5, point.y - 5); ctx.lineTo(point.x - 5, point.y + 5)
        ctx.stroke()
        ctx.strokeStyle = amber
        ctx.lineWidth = 2.5
        ctx.beginPath()
        ctx.moveTo(point.x - 5, point.y - 5); ctx.lineTo(point.x + 5, point.y + 5)
        ctx.moveTo(point.x + 5, point.y - 5); ctx.lineTo(point.x - 5, point.y + 5)
        ctx.stroke()
      }
      ctx.restore()
    },
  }

  chart = new Chart(el.value, {
    type: 'line',
    plugins: [censorMarks],
    data: { datasets: [
      { label: '95% pointwise confidence band', data: lower, borderWidth: 0, pointRadius: 0, stepped: true },
      { label: '95% pointwise confidence band', data: upper, borderWidth: 0, pointRadius: 0, stepped: true, fill: '-1', backgroundColor: 'rgba(81,209,207,.20)' },
      { label: 'Kaplan–Meier estimate', data: survival, borderColor: '#51d1cf', backgroundColor: '#51d1cf', fill: false, stepped: true, pointRadius: 3, pointHoverRadius: 5, borderWidth: 2.5 },
      { label: 'Right-censored observation', data: censored, borderWidth: 0, showLine: false, pointRadius: 0, pointHoverRadius: 7, pointStyle: 'cross', pointBackgroundColor: amber, pointBorderColor: amber },
    ] },
    options: {
      animation: false,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: muted, font: { family: 'Inter', size: 11 }, boxWidth: 12, usePointStyle: true, filter: item => item.datasetIndex !== 0 } },
        tooltip: { callbacks: { label: context => {
          const datum: any = context.raw
          return datum.n_at_risk === undefined ? context.dataset.label : `${Math.round(datum.y * 100)}% · ${datum.n_at_risk} at risk · ${datum.n_events} events · ${datum.n_censored} censored`
        } } },
      },
      scales: {
        x: { type: 'linear', title: { display: true, text: 'Run life (days)', color: muted }, ticks: { color: muted, font: { family: 'JetBrains Mono' } }, grid: { color: grid } },
        y: { min: 0, max: 1, title: { display: true, text: 'Survival probability', color: muted }, ticks: { callback: value => `${Math.round(Number(value) * 100)}%`, color: muted, font: { family: 'JetBrains Mono' } }, grid: { color: grid } },
      },
    },
  })
}

onMounted(render)
watch(() => props.estimate, render, { deep: true })
onBeforeUnmount(() => chart?.destroy())
</script>

<template><div class="chart-wrap survival-chart"><canvas ref="el" role="img" aria-label="Kaplan-Meier run-life survival curve"/></div></template>
