<script setup lang="ts">
import { Chart, LineController, LineElement, PointElement, LinearScale, Tooltip, Legend, Filler, type Plugin } from 'chart.js'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Candidate, PumpCurve } from '../lib/types'
Chart.register(LineController,LineElement,PointElement,LinearScale,Tooltip,Legend,Filler)
const props=defineProps<{candidate:Candidate;curve:PumpCurve}>()
const el=ref<HTMLCanvasElement>(); const caveats=ref<HTMLElement>(); let chart:Chart|undefined
function render(){
  if(!el.value)return
  chart?.destroy()
  const css=getComputedStyle(document.documentElement), muted=css.getPropertyValue('--muted').trim(), grid=css.getPropertyValue('--line').trim()
  const mobile=el.value.parentElement!.clientWidth<560
  const caveatPadding=Math.max(mobile?156:96,(caveats.value?.offsetHeight ?? 0)+14)
  const {curve}=props
  const zonePlugin:Plugin<'line'>={id:'thrustZones',beforeDatasetsDraw(c){
    const {ctx,chartArea,scales:{x}}=c
    const low=x.getPixelForValue(curve.q_min_bpd), down=x.getPixelForValue(curve.downthrust_limit_bpd), up=x.getPixelForValue(curve.upthrust_limit_bpd), high=x.getPixelForValue(curve.q_max_bpd)
    ctx.save()
    ctx.fillStyle='rgba(197,86,79,.20)';ctx.fillRect(low,chartArea.top,down-low,chartArea.bottom-chartArea.top)
    ctx.fillStyle='rgba(71,161,157,.10)';ctx.fillRect(down,chartArea.top,up-down,chartArea.bottom-chartArea.top)
    ctx.fillStyle='rgba(197,86,79,.20)';ctx.fillRect(up,chartArea.top,high-up,chartArea.bottom-chartArea.top)
    const bep=x.getPixelForValue(curve.bep_q_bpd);ctx.strokeStyle='#e1ab55';ctx.lineWidth=1.5;ctx.setLineDash([5,4]);ctx.beginPath();ctx.moveTo(bep,chartArea.top);ctx.lineTo(bep,chartArea.bottom);ctx.stroke()
    ctx.restore()
  },afterDatasetsDraw(c){
    const {ctx}=c
    const points=c.getDatasetMeta(1).data
    const start=points[0],end=points.at(-1)
    if(!start||!end)return
    const sx=start.x,sy=start.y,ex=end.x,ey=end.y
    const angle=Math.atan2(ey-sy,ex-sx)
    ctx.save()
    ctx.font='600 10px JetBrains Mono'
    ctx.lineWidth=2
    ctx.strokeStyle='#e4eef2'
    ctx.fillStyle='#0d151c'
    ctx.beginPath();ctx.arc(sx,sy,5,0,Math.PI*2);ctx.fill();ctx.stroke()
    ctx.fillStyle='#e4eef2';ctx.fillText(`M${Math.round((props.candidate.cells_sampled[0]?.month) ?? 0)} start`,sx+8,sy-8)
    ctx.strokeStyle='#51d1cf';ctx.fillStyle='#51d1cf'
    ctx.beginPath();ctx.moveTo(ex,ey);ctx.lineTo(ex-8*Math.cos(angle-.5),ey-8*Math.sin(angle-.5));ctx.lineTo(ex-8*Math.cos(angle+.5),ey-8*Math.sin(angle+.5));ctx.closePath();ctx.fill()
    ctx.fillText(`M${Math.round((props.candidate.cells_sampled.at(-1)?.month) ?? 0)} end`,ex+8,ey+14)
    ctx.restore()
  }}
  const curvePoints=curve.points.map(p=>({x:p.q_bpd,y:p.head_ft_total}))
  const operating=props.candidate.cells_sampled.filter(p=>p.envelope==='base' && p.converged!==false).map(p=>({x:p.total_fluid_intake_bpd??p.liquid_rate_bpd,y:p.head_developed_ft,month:p.month,rate:p.total_fluid_intake_bpd??p.liquid_rate_bpd,zone:p.zone}))
  chart=new Chart(el.value,{type:'line',plugins:[zonePlugin],data:{datasets:[
    {label:'Reference head curve (without gas derating)',data:curvePoints,borderColor:muted,borderWidth:2,pointRadius:0,tension:.18},
    {label:'Operating path · M0 → final month',data:operating,borderColor:'#51d1cf',backgroundColor:'#51d1cf',pointRadius:2,pointHoverRadius:6,borderWidth:2.5,tension:.18}
  ]},options:{animation:false,maintainAspectRatio:false,layout:{padding:{top:caveatPadding}},interaction:{mode:'nearest',intersect:false},plugins:{legend:{labels:{color:muted,font:{family:'Inter',size:11},boxWidth:12}},tooltip:{callbacks:{label:(c)=>{const d:any=c.raw;return d.month===undefined?`${c.dataset.label}: ${Math.round(d.y)} ft`:`${d.month} mo · ${Math.round(d.rate)} bpd · ${d.zone.replaceAll('_',' ')}`}}}},scales:{x:{type:'linear',min:curve.q_min_bpd,max:curve.q_max_bpd,title:{display:true,text:'Liquid rate (bpd)',color:muted,font:{family:'JetBrains Mono'}},ticks:{color:muted,font:{family:'JetBrains Mono'},maxTicksLimit:mobile?4:7},grid:{color:grid}},y:{title:{display:true,text:'Total head (ft)',color:muted,font:{family:'JetBrains Mono'}},ticks:{color:muted,font:{family:'JetBrains Mono'},maxTicksLimit:mobile?5:7},grid:{color:grid}}}}})
}
onMounted(async()=>{await nextTick();render()}); watch(()=>[props.candidate,props.curve],async()=>{await nextTick();render()},{deep:true});onBeforeUnmount(()=>chart?.destroy())
</script>
<template><div class="chart-wrap pump-chart"><canvas ref="el" aria-label="Catalog pump head curve with deterministic operating path and thrust zones" role="img"/><section ref="caveats" class="chart-qualifications" aria-label="Curve qualifications"><strong>Curve qualification — {{curve.data_quality.replaceAll('_',' ')}}</strong><p v-for="caveat in curve.caveats" :key="caveat">{{caveat}}</p></section><div class="curve-zone-key" aria-hidden="true"><span><i class="zone-down"></i>downthrust</span><span><i class="zone-range"></i>between limits</span><span><i class="zone-up"></i>upthrust</span><span><i class="zone-bep"></i>BEP</span></div></div></template>
