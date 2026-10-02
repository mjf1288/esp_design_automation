<script setup lang="ts">
import { Chart, LineController, LineElement, PointElement, LinearScale, Tooltip, Legend, type Plugin } from 'chart.js'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Candidate } from '../lib/types'
import { verifiedRecovery } from '../lib/vsd'
Chart.register(LineController, LineElement, PointElement, LinearScale, Tooltip, Legend)
const props = defineProps<{candidate: Candidate}>()
const el = ref<HTMLCanvasElement>()
let chart: Chart | undefined
const boundary: Plugin<'line'> = {id:'boundary', afterDraw(c) {
  const month=props.candidate.validity.valid_until_months
  if(month==null) return
  const x=c.scales.x.getPixelForValue(month), {ctx,chartArea}=c
  const amber=getComputedStyle(document.documentElement).getPropertyValue('--amber').trim()
  ctx.save(); ctx.strokeStyle=amber; ctx.setLineDash([5,4])
  ctx.beginPath(); ctx.moveTo(x,chartArea.top); ctx.lineTo(x,chartArea.bottom); ctx.stroke()
  ctx.fillStyle=amber; ctx.font='12px Inter'
  ctx.fillText(`First baseline failure: month ${month}`,x+6,chartArea.top+16); ctx.restore()
}}
function render(){
  if(!el.value) return
  chart?.destroy()
  const css=getComputedStyle(document.documentElement),muted=css.getPropertyValue('--muted').trim(),grid=css.getPropertyValue('--line').trim()
  const accent=css.getPropertyValue('--accent').trim(),amber=css.getPropertyValue('--amber').trim(),violet=css.getPropertyValue('--violet').trim()
  const cells=props.candidate.cells_sampled.filter(c=>c.envelope==='base').sort((a,b)=>a.month-b.month)
  const recovered=verifiedRecovery(props.candidate)
  chart=new Chart(el.value,{
    type:'line', plugins:[boundary],
    data:{datasets:[
      {label:`Pump head at ${props.candidate.configuration.frequency_hz} Hz`,data:cells.map(c=>({x:c.month,y:c.converged===false?null:c.head_developed_ft})),borderColor:accent,pointRadius:2,tension:0},
      {label:'Required TDH',data:cells.map(c=>({x:c.month,y:c.head_required_ft})),borderColor:amber,pointRadius:2,tension:0},
      ...(recovered.length?[{label:'VSD: verified sample points',data:recovered.map(p=>({x:p.month,y:p.head_developed_ft})),borderColor:violet,backgroundColor:violet,showLine:false,pointRadius:5,pointStyle:'rectRot' as const}]:[])
    ]},
    options:{animation:false,maintainAspectRatio:false,plugins:{
      tooltip:{callbacks:{label:c=>{
        const p=c.datasetIndex===2?recovered[c.dataIndex]:null
        return `${c.dataset.label}: ${Math.round(c.parsed.y??0)} ft${p?` at ${p.frequency_hz} Hz`:''}`
      }}},
      legend:{position:'bottom',labels:{color:muted,font:{family:'Inter',size:12},boxWidth:12}}
    },scales:{
      x:{type:'linear',min:0,max:props.candidate.validity.horizon_months,title:{display:true,text:'Months from installation',color:muted},ticks:{color:muted},grid:{color:grid}},
      y:{title:{display:true,text:'Head (ft)',color:muted},ticks:{color:muted},grid:{color:grid}}
    }}
  })
}
onMounted(render)
watch(()=>props.candidate,render,{deep:true})
onBeforeUnmount(()=>chart?.destroy())
</script>
<template><div class="chart-wrap timeline-chart"><canvas ref="el" role="img" aria-label="Baseline pump head versus required TDH, with verified VSD sample points"/></div></template>
