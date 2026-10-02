<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useEspStore } from '../stores'
import SurvivalChart from '../components/SurvivalChart.vue'

const store=useEspStore()
const note=ref('')
const installDate=ref('')
const observedDate=ref('')
const status=ref('')
const candidate=computed(()=>store.selected)
const pumpModel=computed(()=>candidate.value?.configuration.pump_model ?? '')
onMounted(()=>store.loadSurvival())
watch(pumpModel,()=>store.loadSurvival())

async function submit(){
  if(!pumpModel.value || !installDate.value || !observedDate.value){
    status.value='Pump selection, installation date, and observation date are required before recording evidence.'
    return
  }
  const saved=await store.addObservation({
    external_case_id:store.caseData.metadata?.case_id,
    pump_model:pumpModel.value,
    case_inputs_snapshot:store.caseData,
    selected_configuration:candidate.value?.configuration,
    install_date:installDate.value,
    still_running:true,
    outcome_observed_date:observedDate.value,
    is_failure:false,
    operating_conditions:{},
    source:{value:'Field observation entered in ESP Design Automation',unit:null,source:'engineer_override',note:note.value || null},
    engineer_commentary:note.value || null
  })
  if(saved) await store.loadSurvival()
  status.value=saved?'Observation submitted as a right-censored, still-running record. The survival evidence was refreshed.':'Observation could not be saved. Fixture mode never writes empirical evidence.'
}
</script>

<template>
<section class="view" data-testid="view-empirical">
  <div class="view-heading">
    <div><p class="eyebrow">05 / empirical overlay</p><h1>Learned evidence, never calculation</h1><p>Empirical rules annotate deterministic facts; they cannot replace them.</p></div>
    <span class="status-pill judgment-tag">judgments</span>
  </div>

  <div class="empirical-grid">
    <article class="panel">
      <div class="panel-title"><div><p class="eyebrow">survival evidence</p><h2>Run-life survival</h2></div><span class="judgment-tag">judgment-supporting evidence</span></div>
      <div v-if="store.survivalState==='loading'" class="chart-loading">Loading tenant-scoped Kaplan–Meier evidence…</div>
      <SurvivalChart v-else-if="store.survival?.estimate" :estimate="store.survival.estimate"/>
      <div v-else class="empty-state empirical-empty-state">{{store.survival?.explanation ?? (store.survivalError ? `Survival evidence could not be loaded: ${store.survivalError}` : 'Live survival estimates require the empirical API. Fixture preview does not substitute a survival curve.') }}</div>
      <p class="chart-note">Kaplan–Meier curves include right-censored still-running installations; amber × marks identify censored follow-up. Confidence bands are empirical evidence, never deterministic pump facts.</p>
      <template v-if="store.survival?.estimate"><p class="survival-summary number">{{store.survival.estimate.n_observations}} observations · {{store.survival.estimate.n_failures}} failures · {{store.survival.estimate.n_right_censored}} right-censored</p><ul v-if="store.survival.estimate.warnings.length" class="survival-warnings"><li v-for="warning in store.survival.estimate.warnings" :key="warning">{{warning}}</li></ul></template>
    </article>

    <article class="observation panel">
      <div class="panel-title"><div><p class="eyebrow">new field observation</p><h2>Capture evidence</h2></div><span class="fact-label">auditable input</span></div>
      <p>Creates a still-running, right-censored observation. Dates and the current case/configuration snapshot are passed to the tenant-scoped endpoint.</p>
      <label>Pump model <input :value="pumpModel" readonly aria-label="Selected pump model"/></label>
      <div class="form-grid"><label>Install date <input v-model="installDate" type="date" data-testid="input-install-date"/></label><label>Observation date <input v-model="observedDate" type="date" data-testid="input-observation-date"/></label></div>
      <label>Engineer commentary <textarea v-model="note" placeholder="Enter field observation or teardown context…" data-testid="input-observation"/></label>
      <button class="primary" @click="submit" data-testid="button-submit-observation">Record still-running observation</button>
      <p v-if="status" class="muted" aria-live="polite">{{status}}</p>
    </article>
  </div>

  <section class="rules">
    <div class="panel-title"><div><p class="eyebrow">derived associations</p><h2>Rules + confidence basis</h2></div><span class="judgment-tag">computed confidence</span></div>
    <article v-for="r in store.rules" :key="r.rule_id" class="empirical-rule">
      <div><p class="eyebrow">empirical judgment / {{r.rule_id}}</p><strong>{{r.statement}}</strong><p>{{r.evidence.confidence_basis}}</p></div>
      <div class="rule-metrics"><span class="confidence">{{Math.round(r.evidence.evidence_strength*100)}}% confidence</span><span class="number">{{r.evidence.n_observations}} obs · {{r.evidence.n_distinct_fields}} fields</span></div>
      <div v-if="r.bias_flags?.length" class="biases"><span v-for="flag in r.bias_flags" :key="flag">{{flag.replaceAll('_',' ')}}<small>{{r.bias_explanations?.[flag]}}</small></span></div>
    </article>
    <div v-if="!store.rules.length" class="empty-state empirical-empty-state">No empirical rules matched this configuration and context.</div>
  </section>
</section>
</template>
