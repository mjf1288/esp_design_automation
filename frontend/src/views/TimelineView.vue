<script setup lang="ts">
import { computed } from 'vue'
import { useEspStore } from '../stores'
import TimelineChart from '../components/TimelineChart.vue'
import { verifiedRecovery } from '../lib/vsd'
const store=useEspStore()
const c=computed(()=>store.selected)
const recovery=computed(()=>c.value?verifiedRecovery(c.value):[])
const current=computed(()=>['0.1.1','0.2.0'].includes(store.design.provenance.engine_version))
const rows=computed(()=>{
  if(!c.value) return []
  const months=new Set(c.value.timeline.map(t=>t.month))
  if(c.value.validity.valid_until_months!=null) months.add(c.value.validity.valid_until_months)
  const last=recovery.value.at(-1)?.month
  if(last!=null){months.add(last); months.add(last+1)}
  return c.value.cells_sampled.filter(x=>x.envelope==='base' && months.has(x.month)).sort((a,b)=>a.month-b.month)
})
const recoveryAt=(month:number)=>recovery.value.find(p=>p.month===month)
</script>
<template>
<section class="view" data-testid="view-timeline">
  <div class="view-heading">
    <div><p class="eyebrow">04 / validity timeline</p><h1>Head sufficiency and VSD recovery</h1><p>Near BEP does not mean sufficient head. Baseline and frequency-adjusted results are shown separately.</p></div>
    <span class="status-pill fact">rank #{{c?.rank ?? '—'}}</span>
  </div>
  <template v-if="c">
    <div class="catalog-warning"><strong>PROTOTYPE</strong><span>Estimated pump curves. Screening results, not a field-release approval.</span></div>
    <div v-if="!current" class="catalog-warning" data-testid="legacy-validity-warning"><strong>RECALCULATE</strong><span>This stored run predates the validity correction. Its recovery and validity claims are unverified.</span><button class="text-button" @click="store.runDesign(store.caseData.metadata?.case_id)">Recalculate</button></div>
    <template v-else>
      <div class="validity-summary" data-testid="validity-summary">
        <div><span>Baseline · {{c.configuration.frequency_hz}} Hz</span><strong>{{c.validity.valid_until_months!=null?`First failure: month ${c.validity.valid_until_months}`:'No failure in sampled horizon'}}</strong><small>{{c.validity.limiting_mechanism}}</small></div>
        <div><span>VSD · original installed equipment</span><strong>{{recovery.length?`Last verified sample: month ${recovery.at(-1)?.month}`:c.vsd_recovery?.assessment==='not_needed'?'Recovery not needed':!c.vsd_recovery?'No VSD available':'No verified recovery'}}</strong><small v-if="recovery.length">{{recovery[0].frequency_hz}}–{{recovery.at(-1)?.frequency_hz}} Hz across recovered samples. No recovery is claimed beyond the last verified month.</small><small v-else>{{c.vsd_recovery?.note}}</small></div>
      </div>
      <article class="panel timeline-panel">
        <div class="panel-title"><div><p class="eyebrow">base scenario / calculated</p><h2>Pump head versus required TDH</h2></div><span>Fixed pump, motor and cable</span></div>
        <TimelineChart :candidate="c"/>
        <p class="chart-note"><template v-if="recovery.length">Purple diamonds show recalculated head at tested VSD frequencies, not a continuous operating guarantee. </template><template v-else>No verified VSD recovery points are shown. </template><template v-if="c.validity.valid_until_months!=null">The dashed marker is the first failed baseline sample.</template><template v-else>No baseline failure was found at the sampled months.</template></p>
      </article>
      <article class="timeline-table panel">
        <div class="panel-title"><h2>Baseline versus VSD checkpoints</h2><span>Head in ft · base scenario</span></div>
        <div class="table-scroll"><table>
          <thead><tr><th>Month</th><th>Baseline head</th><th>Required TDH</th><th>Head margin</th><th>Q/Q<sub>BEP</sub></th><th>VSD result</th></tr></thead>
          <tbody><tr v-for="x in rows" :key="x.month">
            <td class="number">{{x.month===0?'Day 1':x.month}}</td>
            <td class="number">{{x.converged===false?'Uncomputed':Math.round(x.head_developed_ft)}}</td><td class="number">{{Math.round(x.head_required_ft)}}</td>
            <td :class="['number',x.head_developed_ft<x.head_required_ft?'head-deficit':'']">{{x.converged===false?'Uncomputed':((x.head_developed_ft/x.head_required_ft-1)*100).toFixed(1)+'%'}}{{x.converged!==false && x.head_developed_ft<x.head_required_ft?' · shortfall':''}}</td>
            <td class="number">{{x.converged===false?'Uncomputed':x.q_over_qbep.toFixed(2)}}</td>
            <td><template v-if="recoveryAt(x.month)">{{recoveryAt(x.month)?.frequency_hz}} Hz → {{Math.round(recoveryAt(x.month)!.head_developed_ft)}} ft · passes model checks</template><template v-else>{{c.validity.valid_until_months==null || x.month<c.validity.valid_until_months?'Not needed':'Not recovered'}}</template></td>
          </tr></tbody>
        </table></div>
      </article>
      <details v-if="recovery.length" class="panel recovery-details"><summary>Show verified frequency schedule and equipment checks</summary>
        <p class="chart-note">Motor load, cable voltage drop, winding temperature and conductor temperature are recalculated for the original equipment. Vendor-data gaps and unmodeled field limits remain unresolved.</p>
        <div class="table-scroll"><table><thead><tr><th>Month</th><th>Hz</th><th>Motor load</th><th>Cable drop</th><th>Winding °F</th><th>Cable °F</th></tr></thead>
          <tbody><tr v-for="p in recovery" :key="p.month"><td>{{p.month}}</td><td>{{p.frequency_hz}}</td><td>{{(p.motor_loading_frac*100).toFixed(1)}}%</td><td>{{(p.cable_voltage_drop_frac*100).toFixed(1)}}%</td><td>{{p.motor_winding_temp_f.toFixed(0)}}</td><td>{{p.cable_conductor_temp_f.toFixed(0)}}</td></tr></tbody>
        </table></div>
      </details>
    </template>
  </template>
  <div v-else class="empty-state">No timeline is available because no candidate is selected.</div>
</section>
</template>
<style scoped>
.validity-summary{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px;padding:18px;border:1px solid var(--line);border-radius:var(--radius);background:var(--surface)}
.validity-summary span,.validity-summary strong,.validity-summary small{display:block}
.validity-summary span{font-size:12px;color:var(--muted);margin-bottom:7px}
.validity-summary strong{font-size:18px;margin-bottom:7px}
.validity-summary small{font-size:12px;color:var(--muted);line-height:1.5}
.head-deficit{color:var(--amber)}
.recovery-details{margin-top:20px}.recovery-details summary{cursor:pointer;font-size:14px}
.timeline-chart{height:280px}
@media(max-width:700px){.validity-summary{grid-template-columns:1fr}.view-heading{flex-wrap:wrap}}
</style>
