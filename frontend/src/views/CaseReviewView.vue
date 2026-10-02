<script setup lang="ts">
import { computed, ref } from 'vue'
import { useEspStore } from '../stores'
const store=useEspStore();const edit=ref<Record<string,string>>({})
// Framework 3.0 is a class of its own and belongs at the top: it is the data
// everything else is computed from, and the one class the design engineer is
// explicitly not responsible for assessing. `measured:true` suppresses the
// override control -- 3.0 says the engineer works with what is given and does
// not dispute it, so the UI must not invite a silent correction.
const groups=computed(()=>[{name:'MEASURED',sub:'The customer\u2019s factual data \u00b7 recorded, not assessed',obj:{reservoir:store.caseData.reservoir,fluid:store.caseData.fluid,geometry:store.caseData.geometry},rigidity:'given',measured:true},{name:'EXPECTATIONS',sub:'Design targets · may be proven unrealistic',obj:store.caseData.expectations,rigidity:'target'},{name:'CONSTRAINTS',sub:'Enforced limits with rigidity',obj:store.caseData.constraints,rigidity:'hard'},{name:'COMPLICATIONS',sub:'Challenges that change calculation and selection',obj:store.caseData.complications,rigidity:'bias'}])
function flatten(x:any,p=''):Array<[string,any]>{if(x&&typeof x==='object'&&'value'in x&&'source'in x)return[[p,x]];if(Array.isArray(x))return x.flatMap((v,i)=>flatten(v,`${p}[${i}]`));if(x&&typeof x==='object')return Object.entries(x).flatMap(([k,v])=>flatten(v,p?`${p}.${k}`:k));return[]}
const trustRegister=computed(()=>store.design.trust_register??[])
// Rendered flat, the register repeated the same class sentence eleven times and
// the repetition read as noise rather than as a boundary. Group by class and
// print the sentence once per group -- when every row in a class shares an owner
// description, that description belongs to the class, not to each row.
const trustGroups=computed(()=>{const out:Array<{cls:string;shared:string|null;rows:any[]}>=[];for(const e of trustRegister.value){const g=out.find(x=>x.cls===e.data_class);if(g)g.rows.push(e);else out.push({cls:e.data_class,shared:null,rows:[e]})}
  // Derive the shared sentence from the rows that did NOT cross the boundary. A
  // crossing row has a different owner by construction, so including it would
  // defeat the grouping for exactly the class the grouping matters most in --
  // MEASURED, where two system-filled gaps would push eleven genuine
  // customer-measured rows back to repeating the same line each.
  for(const g of out){const owners=new Set(g.rows.filter(r=>!r.boundary_note).map(r=>r.accuracy_owner));g.shared=owners.size===1?[...owners][0]:null}
  return out})
function save(path:string){store.recordOverride(path,edit.value[path]??'')}
</script>
<template><section class="view" data-testid="view-case-review"><div class="view-heading"><div><p class="eyebrow">02 / engineer review</p><h1>Input classes remain separate</h1><p>An override is a deliberate new provenance record, never a silent edit.</p></div><span class="status-pill fact">input confidence {{Math.round(store.design.input_confidence*100)}}%</span></div>
<div class="class-panels"><article v-for="g in groups" :key="g.name" class="class-panel"><header><div><p class="eyebrow">{{g.rigidity}}</p><h2>{{g.name}}</h2><p>{{g.sub}}</p></div></header><div class="review-rows"><div v-for="[path,item] in flatten(g.obj)" :key="path" class="review-row" :class="{assumption:item.source==='assumption'||item.source==='default'}"><div><strong>{{path.split('.').at(-1)?.replaceAll('_',' ')}}</strong><span class="source">{{item.source.replaceAll('_',' ')}} · {{Math.round(item.confidence*100)}}%</span></div><div class="review-value"><span class="number">{{item.value}} {{item.unit}}</span><template v-if="!g.measured&&(item.source==='assumption'||item.source==='default')"><input v-model="edit[path]" :placeholder="String(item.value)" :aria-label="`Override ${path}`"/><button class="text-button" @click="save(path)">Override</button></template></div><p v-if="item.assumption" class="assumption-detail">basis {{item.assumption.basis}} · biased {{item.assumption.bias}} · {{item.assumption.rationale}}</p></div></div></article></div>
<section v-if="trustRegister.length" class="panel trust-register">
  <div class="panel-title"><h2>Taken on trust</h2><span>framework 3.0 · {{trustRegister.length}} inputs</span></div>
  <p class="trust-intro">These values were used as given. This is a different list from the
  assumption ledger: there, the system chose a number and invites challenge. Here it used
  someone else&rsquo;s number and is not in a position to check it.</p>
  <div v-for="g in trustGroups" :key="g.cls" class="trust-group">
  <p class="trust-group-head"><span class="trust-tag">{{g.cls}}</span><span v-if="g.shared">{{g.shared}}</span></p>
  <div v-for="e in g.rows" :key="e.field_path" class="trust-row" :class="{crossing:!!e.boundary_note}">
    <div class="trust-head"><strong>{{e.field_path}}</strong><span class="number">{{e.value}} {{e.unit}}</span></div>
    <p v-if="e.accuracy_owner!==g.shared" class="trust-owner">{{e.accuracy_owner}}</p>
    <p v-if="e.transcription_owned_by_system" class="trust-owner">Read from a document by this system &mdash; the quantity is the customer&rsquo;s, the transcription is ours and is reviewable against the quoted span.</p>
    <p v-if="e.boundary_note" class="trust-crossing">{{e.boundary_note}}</p>
  </div>
  </div>
</section>
<section v-if="store.overrideLog.length" class="panel compact"><div class="panel-title"><h2>Recorded overrides</h2><span>pending service confirmation</span></div><p v-for="x in store.overrideLog" :key="x" class="number">{{x}}</p></section></section></template>
