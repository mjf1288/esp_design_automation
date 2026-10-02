import { Chart } from 'chart.js'

// Source tags are visible text, not hover-only metadata. Observe text changes
// without rewriting Vue's text nodes or its event-bound controls.
export function installSyntheticLabels(root:HTMLElement){
  const annotate=()=>{
    root.querySelectorAll<HTMLElement>('.view .number,.view td,.view dd,.view p:not(.eyebrow),.view .candidate-diff,.view .validity-summary span,.view .validity-summary strong,.view .validity-summary small').forEach(el=>{
      el.classList.toggle('synthetic-number',!!el.closest('.synthetic-scope')&&/\d/.test(el.textContent??''))
    })
  }
  new MutationObserver(annotate).observe(root,{childList:true,subtree:true,characterData:true})
  annotate()
}
Chart.register({id:'syntheticProvenance',beforeUpdate(chart){
  if(!chart.canvas.closest('.synthetic-scope'))return
  for(const scale of Object.values(chart.options.scales??{})){
    if(!scale)continue
    const ticks=scale.ticks as any
    if(!ticks||(ticks.callback as any)?.syntheticTag)continue
    const original=ticks.callback
    const tagged=function(this:any,...args:any[]){
      const label=original?original.apply(this,args):args[0]
      return `${label} SYN`
    }
    tagged.syntheticTag=true;ticks.callback=tagged
  }
}})
