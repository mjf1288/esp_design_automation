<script setup lang="ts">
import {useEspStore} from '../stores'
const store=useEspStore()
</script>
<template><div class="demo-case-bar"><label for="demo-case">Demonstration case</label><select id="demo-case" data-testid="demo-case" :value="store.caseData.metadata?.case_id" @change="store.load(($event.target as HTMLSelectElement).value)">
<option value="synthetic-replacement">SYN-A · Replacement with history</option><option value="synthetic-new">SYN-B · New well, uncertain trajectory</option><option value="synthetic-gassy">SYN-C · Gassy well, section screening</option><option value="demo-permian-h12">Historical estimated-data demo</option></select><strong v-if="(store.caseData as any).synthetic">SYNTHETIC DEMO ONLY</strong><span>Selection is not field approval.</span></div></template>
<style scoped>.demo-case-bar{display:flex;align-items:center;flex-wrap:wrap;gap:12px;padding:12px 28px;border-bottom:1px solid var(--line);font-size:12px}.demo-case-bar strong{color:var(--warning)}select{max-width:100%;padding:8px;background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:4px}</style>
