<script setup lang="ts">
import { computed } from 'vue'
import type { Candidate, RankingStability } from '../lib/types'

const props = defineProps<{ candidate:Candidate; rankingStability?:RankingStability|null }>()

const t = computed(()=>props.candidate.tolerance ?? null)

const BASIS_LABEL:Record<string,string> = {
  vendor_published:'vendor published curve',
  unit_test_report:'this unit’s test report',
  parametric_estimate:'parametric reconstruction',
  undeclared:'undeclared',
}

const EFFECT_LABEL:Record<string,string> = {
  flips:'decision flips',
  holds:'decision holds',
  no_consumer:'nothing reads it',
  not_computable:'not attributable',
}

const CHANNEL_LABEL:Record<string,string> = {
  stage_count:'stage count',
  head_margin:'head margin',
  operating_zone:'operating zone',
  motor_loading:'motor loading',
  efficiency_ranking:'efficiency ranking',
}

const material = computed(()=>(t.value?.findings ?? []).filter(f=>f.effect==='flips'))
const settled  = computed(()=>(t.value?.findings ?? []).filter(f=>f.effect!=='flips'))

function pct(x?:number|null){ return x==null ? '—' : `${(x*100).toFixed(1)}%` }
</script>

<template>
<section v-if="t" class="tolerance" aria-label="Acceptance-tolerance materiality">
  <div class="panel-title">
    <div>
      <p class="eyebrow">6B.2 / manufacturing acceptance tolerance</p>
      <h2>Does this design survive the tolerance the standard permits?</h2>
    </div>
    <span class="fact-label">computed fact</span>
  </div>

  <!-- Provenance gate. The band belongs to the curve, not to the pump. -->
  <dl class="tol-figures">
    <div><dt>standard</dt><dd>{{t.standard}}</dd></div>
    <div><dt>curve basis</dt><dd>{{BASIS_LABEL[t.curve_basis] ?? t.curve_basis}}</dd></div>
    <div>
      <dt>band applied</dt>
      <dd :class="{'tol-not-applied':!t.band_applied}">{{t.band_applied ? 'yes' : 'no'}}</dd>
    </div>
    <div v-if="t.band_applied">
      <dt>decisions the band unsettles</dt>
      <dd>{{material.length}} of {{t.findings.length}}</dd>
    </div>
  </dl>

  <!-- Not a cleared check. The band was never asserted. -->
  <p v-if="!t.band_applied" class="tol-unbounded">
    <strong>The acceptance band is not in force for this curve.</strong>
    {{t.recommendation}}
  </p>

  <template v-else>
    <p class="tol-recommendation">{{t.recommendation}}</p>

    <ul v-if="material.length" class="tol-findings">
      <li v-for="f in material" :key="f.channel" class="tol-material">
        <div class="tol-head">
          <strong>{{CHANNEL_LABEL[f.channel] ?? f.channel}}</strong>
          <span class="tol-effect">{{EFFECT_LABEL[f.effect]}}</span>
          <span class="number tol-band">±{{pct(f.band_frac)}} permitted</span>
          <span v-if="f.flip_at_frac!=null" class="number tol-band">flips at {{pct(f.flip_at_frac)}}</span>
          <span v-if="f.at_month!=null" class="number tol-band">month {{f.at_month}}</span>
        </div>
        <p>{{f.detail}}</p>
      </li>
    </ul>

    <ul v-if="settled.length" class="tol-findings tol-settled-list">
      <li v-for="f in settled" :key="f.channel">
        <div class="tol-head">
          <strong>{{CHANNEL_LABEL[f.channel] ?? f.channel}}</strong>
          <span class="tol-effect">{{EFFECT_LABEL[f.effect]}}</span>
          <span v-if="f.flip_at_frac!=null" class="number tol-band">would need {{pct(f.flip_at_frac)}}</span>
        </div>
        <p>{{f.detail}}</p>
      </li>
    </ul>
  </template>

  <!-- Ranking stability is run-level, reported here because it qualifies the order. -->
  <p v-if="rankingStability" class="tol-stability">
    <span class="eyebrow">ranking under the band</span>
    <template v-if="!rankingStability.assessed">not assessed — {{rankingStability.detail}}</template>
    <template v-else>
      <strong>{{rankingStability.stable ? 'order holds' : 'order is not settled'}}</strong>
      <span class="number">
        gap {{rankingStability.score_gap?.toFixed(6) ?? '—'}} ·
        swing {{rankingStability.induced_swing?.toFixed(6) ?? '—'}} ·
        tie tolerance {{rankingStability.tie_tolerance}}
      </span>
      {{rankingStability.detail}}
    </template>
  </p>

  <!-- Scope and model limits travel with the numbers, never separately. -->
  <ul v-if="t.disclosures.length" class="tol-disclosures">
    <li v-for="d in t.disclosures" :key="d">{{d}}</li>
  </ul>
</section>
</template>
