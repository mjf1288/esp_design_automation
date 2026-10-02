import { defineStore } from 'pinia'
import { fixtureCase, fixtureDesign, fixtureRules } from './lib/fixture'
import type { CaseData, Deployment, DesignData, PumpCurve, Rule, SurvivalResponse } from './lib/types'

export const API = '__PORT_8000__'.startsWith('__') ? 'http://localhost:8000' : '__PORT_8000__'
type LoadState = 'loading'|'running'|'no-run'|'ready'|'empty'|'error'
function unwrap<T>(payload: unknown): T { return ((payload as any)?.data ?? (payload as any)?.result ?? payload) as T }
// Two-level perimeter headers (framework v0.3 §9.2). The seeded demo data lives
// in the demo org's demo-operator capsule; the old flat 'X-Tenant-Id: demo' now
// resolves to a different, empty capsule.
const headers={Accept:'application/json','X-Org-Id':'demo','X-Operator-Id':'demo-operator'}
async function request<T>(path:string,init:RequestInit={}):Promise<T> { const r=await fetch(`${API}${path}`,{...init,headers:{...headers,...init.headers}}); if(!r.ok){ let detail=''; try{ const value=(await r.json())?.detail; detail=Array.isArray(value)?value.map(x=>`${x.loc?.join('.')}: ${x.msg}`).join(';'):String(value??'') }catch{} throw new Error(detail?`HTTP ${r.status}: ${detail}`:`HTTP ${r.status}`) } return unwrap<T>(await r.json()) }
// A design run is a two-stage job (api/jobs.py): the deterministic facts are
// stored in under a second, the narrative attaches later. The UI shows facts as
// soon as they exist and tracks the narrative separately.
export type Job={job_id:string;kind:string;case_id:string;status:'queued'|'running'|'succeeded'|'failed'|'cancelled'|'interrupted';phase:string;terminal:boolean;design_id:string|null;facts_ready:boolean;narrative_status:string|null;cancel_requested:boolean;error:string|null;elapsed_s:number;seconds_to_facts:number|null}
const POLL_MS=1500
const sleep=(ms:number)=>new Promise(r=>setTimeout(r,ms))
async function get<T>(path:string):Promise<T> { return request<T>(path) }
function normalizeCase(payload:any):CaseData { const caseData=payload?.case ?? payload; if(payload?.raw_request && caseData?.metadata) return {...caseData,metadata:{...caseData.metadata,raw_request:payload.raw_request}}; return caseData }
function normalizeDesign(payload:any):DesignData { if(!payload?.facts) return payload; const facts=payload.facts; return {...facts,design_id:facts.design_id ?? payload.id,judgments:payload.judgments?.empirical ?? facts.judgments ?? [],provenance:payload.reproducibility ?? facts.provenance} }
export const useEspStore = defineStore('esp', {
  state:()=>({ caseData: fixtureCase as CaseData, design: fixtureDesign as DesignData, rules: fixtureRules as Rule[], curve:null as PumpCurve|null, survival:null as SurvivalResponse|null, deployment:null as Deployment|null, deploymentState:'empty' as LoadState, deploymentError:'', caseState:'ready' as LoadState, designState:'ready' as LoadState, empiricalState:'ready' as LoadState, curveState:'empty' as LoadState, survivalState:'empty' as LoadState, apiError:'', curveError:'', survivalError:'', usingFixture:true, activeView:'results', selectedRank:1, overrideLog:[] as string[], job:null as Job|null, jobError:'', polling:false }),
  getters:{ selected(s){return s.design.candidates.find(c=>c.rank===s.selectedRank) ?? s.design.candidates[0]}, isRefusal(s){return ['target_unachievable','no_viable_configuration','blocked_missing_data'].includes(s.design.verdict)} },
  actions:{
    async setEngineering(options:Record<string,unknown>, geometry?:Record<string,unknown>){
      const id=this.caseData.metadata?.case_id
      if(!id) return
      const data=this.caseData as any
      const updated={...data,engineering:{...data.engineering,...options},
        constraints:geometry?{...data.constraints,geometry:{...data.constraints?.geometry,...geometry}}:data.constraints}
      await request(`/api/cases/${id}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({case:updated,override_reason:'Explicit engineering controls, 2026-10-02 policy'})})
      this.caseData=updated
      await this.runDesign(id)
    },
    async saveProjectPreference(payload:Record<string,unknown>){
      const project=(this.caseData.metadata as any)?.project_id
      if(!project) throw new Error('This case has no project identifier.')
      return request(`/api/projects/${encodeURIComponent(project)}/preferences`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    },
    async loadDeployment(){
      this.deploymentState='loading'; this.deploymentError=''
      try{ this.deployment=await get<Deployment>('/api/deployment'); this.deploymentState='ready' }
      catch(e){ this.deploymentState='error'; this.deploymentError=(e as Error).message }
    },
    async load(caseId='synthetic-replacement', designId?:string){
      this.caseState=this.designState=this.empiricalState='loading'; this.apiError=''
      try {
        // A design run is immutable and takes minutes of enumeration to produce a
        // byte-identical result, so page load must never trigger one. Fetch the
        // newest stored run instead; running the engine is an explicit user action.
        const [casePayload, designPayload, rulesPayload] = await Promise.all([
          get<any>(`/api/cases/${caseId}`),
          designId ? get<any>(`/api/designs/${designId}`) : this.latestStoredDesign(caseId),
          get<Rule[]|{items?:Rule[];rules?:Rule[]}>(`/api/empirical/rules`)
        ])
        // Resume a run started before a reload rather than offering to start a second one.
        const active=await get<{jobs:Job[]}>(`/api/cases/${caseId}/jobs?active=true`).then(r=>r.jobs[0]??null).catch(()=>null)
        if(designPayload===null){
          this.caseData=normalizeCase(casePayload); this.usingFixture=false; this.caseState=this.empiricalState='ready'
          if(active){ this.job=active; this.designState='running'; this.pollJob(caseId) } else this.designState='no-run'
          return
        }
        if(active){ this.job=active; this.pollJob(caseId) }
        this.caseData=normalizeCase(casePayload); this.design=normalizeDesign(designPayload)
        this.rules=Array.isArray(rulesPayload)?rulesPayload:(rulesPayload.items ?? rulesPayload.rules ?? [])
        this.usingFixture=false; this.caseState=this.designState=this.empiricalState='ready'
        await Promise.all([this.loadPumpCurve(),this.loadSurvival()])
      } catch (e) {
        this.apiError=e instanceof Error?e.message:'The API did not return a readable response.'
        if(API==='http://localhost:8000'){
          this.caseData=fixtureCase; this.design=fixtureDesign; this.rules=fixtureRules; this.curve=null; this.survival=null; this.curveState=this.survivalState='empty'; this.usingFixture=true
          this.caseState=this.designState=this.empiricalState='ready'
        } else {
          this.caseState=this.designState=this.empiricalState='error'
        }
      }
    },
    async latestStoredDesign(caseId:string){
      const list=await get<{count:number;designs:{design_id:string}[]}>(`/api/cases/${caseId}/designs`)
      if(!list.count) return null
      return get<any>(`/api/designs/${list.designs[0].design_id}`)
    },
    async runDesign(requestedCaseId?:string){
      const caseId=requestedCaseId ?? this.caseData.metadata?.case_id ?? 'synthetic-replacement'
      // Explicit, user-initiated. The POST only queues the run and returns at once.
      this.designState='running'; this.apiError=''; this.jobError=''
      try {
        const payload=await request<{job:Job}>(`/api/cases/${caseId}/design`,{method:'POST'})
        this.job=payload.job
        await this.pollJob(caseId)
      } catch(e){
        this.apiError=e instanceof Error?e.message:'The design run could not be queued.'
        this.designState='error'
      }
    },
    async pollJob(caseId='demo-permian-h12'){
      if(this.polling || !this.job) return
      this.polling=true
      let shownDesign:string|null=null
      try {
        while(this.job){
          const job:Job=(await request<{job:Job}>(`/api/jobs/${this.job.job_id}`)).job
          this.job=job
          // Facts first: show the design the moment the engine stage has stored it.
          if(job.facts_ready && job.design_id && shownDesign!==job.design_id){
            this.design=normalizeDesign(await get<any>(`/api/designs/${job.design_id}`))
            this.selectedRank=this.design.candidates[0]?.rank ?? 1
            shownDesign=job.design_id; this.designState='ready'; this.usingFixture=false
            await Promise.all([this.loadPumpCurve(),this.loadSurvival()])
          }
          if(job.terminal){
            if(!job.design_id){
              if(job.status==='cancelled'){ this.designState='no-run' }
              else { this.apiError=job.error ?? `The design run ended as ${job.status}.`; this.designState='error' }
            }
            if(job.status!=='succeeded') this.jobError=job.error ?? ''
            break
          }
          await sleep(POLL_MS)
        }
      } catch(e){
        this.jobError=e instanceof Error?e.message:'Lost contact with the design run.'
        if(this.designState==='running'){ this.apiError=this.jobError; this.designState='error' }
      } finally { this.polling=false }
    },
    async cancelJob(){
      if(!this.job || this.job.terminal) return
      try { this.job=(await request<{job:Job}>(`/api/jobs/${this.job.job_id}/cancel`,{method:'POST'})).job }
      catch(e){ this.jobError=e instanceof Error?e.message:'Cancel was not accepted.' }
    },
    async loadPumpCurve(){
      const config=this.selected?.configuration
      if(this.usingFixture || !config?.pump_id){ this.curve=null; this.curveState='empty'; return }
      this.curveState='loading'; this.curveError=''
      try {
        const query=new URLSearchParams({frequency_hz:String(config.frequency_hz),stages:String(config.stages),samples:'80'})
        this.curve=await get<PumpCurve>(`/api/catalog/pumps/${encodeURIComponent(config.pump_id)}/curve?${query}`)
        this.curveState='ready'
      } catch (e) {
        this.curve=null; this.curveState='error'; this.curveError=e instanceof Error?e.message:'The pump-curve response was unreadable.'
      }
    },
    async loadSurvival(){
      const model=this.selected?.configuration.pump_model
      if(this.usingFixture){ this.survival=null; this.survivalState='empty'; return }
      this.survivalState='loading'; this.survivalError=''
      try {
        const query=model?`?${new URLSearchParams({pump_model:model,confidence_level:'0.95'})}`:'?confidence_level=0.95'
        this.survival=await get<SurvivalResponse>(`/api/empirical/survival${query}`)
        this.survivalState=this.survival.estimate?'ready':'empty'
      } catch (e) {
        this.survival=null; this.survivalState='error'; this.survivalError=e instanceof Error?e.message:'The survival response was unreadable.'
      }
    },
    async recordOverride(path:string, value:string){
      if(!value.trim()) return
      this.overrideLog.push(`${path}: ${value}`)
      const id=this.caseData.metadata?.case_id
      if(!id || this.usingFixture) return
      try {
        const edited=structuredClone(this.caseData) as any
        const segments=path.replace(/\[(\d+)\]/g,'.$1').split('.').filter(Boolean)
        let parent:any=edited
        for(const segment of segments.slice(0,-1)) parent=parent?.[segment]
        const key=segments.at(-1)
        const prior=parent?.[key as string]
        if(!prior || typeof prior!=='object' || !('value' in prior)) throw new Error('The selected value no longer has tracked provenance.')
        const priorValue=prior.value
        const coerced=typeof priorValue==='number' ? Number(value) : typeof priorValue==='boolean' ? value==='true' : value
        if(typeof priorValue==='number' && !Number.isFinite(coerced)) throw new Error('A numeric engineering value is required.')
        parent[key as string]={...prior,value:coerced,source:'engineer_override',note:`Engineer override of ${path}`}
        const response=await request<any>(`/api/cases/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({case:edited,override_reason:`Engineer override: ${path}`,engineer_id:'frontend-engineer'})})
        this.caseData=normalizeCase(response)
      } catch (e) { this.apiError=e instanceof Error?`Override was not saved: ${e.message}`:'The override could not be saved to the service.' }
    },
    async addObservation(payload:Record<string,unknown>):Promise<boolean>{
      if(this.usingFixture) return false
      try { const r=await fetch(`${API}/api/empirical/observations`,{method:'POST',headers:{...headers,'Content-Type':'application/json'},body:JSON.stringify(payload)}); return r.ok } catch { return false }
    }
  }
})
