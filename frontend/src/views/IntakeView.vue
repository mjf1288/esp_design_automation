<script setup lang="ts">
import { computed, ref } from 'vue'
import { useEspStore } from '../stores'
import type { Tracked } from '../lib/types'
const store=useEspStore(); const hovered=ref('')
function isTracked(v:any):v is Tracked{return v&&typeof v==='object'&&'value'in v&&'source'in v}
const entries=computed(()=>{const out:Array<{path:string;data:Tracked}>=[];const walk=(x:any,path='')=>{if(isTracked(x))out.push({path,data:x});else if(x&&typeof x==='object')Object.entries(x).forEach(([k,v])=>walk(v,path?`${path}.${k}`:k))};walk(store.caseData);return out})
const hardStops=computed(()=>{
  const caseData=store.caseData as any
  const missing:string[]=[]
  if(!caseData.expectations?.target_rate_bpd) missing.push('target production rate')
  if(!caseData.geometry?.casing_sections?.length) missing.push('casing program (ID vs depth)')
  if(caseData.geometry?.is_vertical!==true && !caseData.geometry?.deviation_survey?.length) missing.push('deviation survey (or explicit confirmation the well is vertical)')
  return [...new Set([...(store.design.blocking_data_gaps ?? []),...missing])]
})
function highlight(text:string){const s=hovered.value;if(!s)return text;const i=text.toLowerCase().indexOf(s.toLowerCase());if(i<0)return text;return [text.slice(0,i),text.slice(i,i+s.length),text.slice(i+s.length)]}
</script>
<template>
<section class="view" data-testid="view-intake"><div class="view-heading"><div><p class="eyebrow">01 / evidence intake</p><h1>Raw request → traced case</h1><p>Every soft value retains a source span. No hard stop is inferred.</p></div><span class="status-pill fact">case {{store.caseData.metadata?.case_id ?? 'absent'}}</span></div>
<div v-if="hardStops.length" class="hard-stop" data-testid="hard-stop-alert"><strong>BLOCKED — missing hard-stop data</strong><span v-for="x in hardStops" :key="x">{{x}}</span><button>Request required data</button></div>
<div class="intake-grid"><article class="raw-pane"><div class="panel-title"><h2>Raw input</h2><span>verbatim</span></div><p class="raw-text"><template v-for="(part,i) in highlight(store.caseData.metadata?.raw_request ?? 'No raw request was returned by the API.')" :key="i"><mark v-if="i===1">{{part}}</mark><template v-else>{{part}}</template></template></p></article>
<article class="case-pane"><div class="panel-title"><h2>Extracted case</h2><span>{{entries.length}} tracked fields</span></div><div class="tracked-list"><button v-for="entry in entries" :key="entry.path" class="tracked-row" :class="{assumption:entry.data.source==='assumption'||entry.data.source==='default'}" @mouseenter="hovered=entry.data.extracted_from ?? ''" @mouseleave="hovered=''"><span class="field">{{entry.path.split('.').at(-1)?.replaceAll('_',' ')}}</span><span class="number">{{entry.data.value}} <small>{{entry.data.unit}}</small></span><span class="source">{{entry.data.source.replaceAll('_',' ')}} · {{Math.round(entry.data.confidence*100)}}%</span><span v-if="entry.data.assumption" class="assumption-detail">basis: {{entry.data.assumption.basis}} · bias: {{entry.data.assumption.bias}}</span></button></div></article></div>
</section>
</template>
