<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useEspStore } from '../stores'

const store = useEspStore()
const d = computed(() => store.deployment)
onMounted(() => { if (store.deploymentState !== 'ready') store.loadDeployment() })
</script>

<template>
  <section class="view">
    <div class="view-heading">
      <div>
        <span class="eyebrow">Deployment posture</span>
        <h1>Where this deployment's data can go</h1>
        <p>
          Reported by the running process, not written by hand: the storage paths and egress
          channels below are read from live objects. Framework §9.6 makes this the first thing a
          customer's security function asks about.
        </p>
      </div>
      <span class="fact-label" v-if="d">§9 PERIMETERS</span>
    </div>

    <div v-if="store.deploymentState === 'loading'" class="loading-view"><i /><i /></div>

    <div v-else-if="store.deploymentState === 'error'" class="error-state">
      <strong>Posture unavailable</strong>
      <p>{{ store.deploymentError }}</p>
    </div>

    <template v-else-if="d">
      <!--
        §9.7 contradiction is a blocking security concern, not a caveat: an
        operator has declared one thing while the deployment does another, and
        the whole point of §9.7 is that reality must be reported first. Danger
        red -- the one place on this page it appears -- not amber, because it
        needs to interrupt a reviewer's scan before they read anything else.
      -->
      <div v-if="d.agentic_layer.posture_contradiction" class="posture-contradiction">
        <div>
          <span class="eyebrow">Posture contradiction</span>
          <strong>Declared mode disagrees with running deployment</strong>
        </div>
        <p><span class="pc-label">Declared</span> <code>{{ d.agentic_layer.declared_mode ?? '(none)' }}</code> <span class="pc-label">Effective</span> <code>{{ d.agentic_layer.effective_mode ?? '(unknown)' }}</code> <span class="pc-label">Endpoint</span> <code>{{ d.agentic_layer.endpoint_location ?? '(unknown)' }}</code></p>
        <p class="pc-reason">{{ d.agentic_layer.posture_contradiction_reason }}</p>
      </div>

      <!-- Egress is the load-bearing disclosure on this page, so it leads. -->
      <div class="posture-lead" :class="d.network_egress_from_design_path ? 'has-egress' : 'no-egress'">
        <div>
          <span class="eyebrow">Network egress from the design path</span>
          <strong class="number">{{ d.network_egress_from_design_path ? 'YES' : 'NONE' }}</strong>
        </div>
        <p v-for="line in d.leaves_the_perimeter" :key="line">{{ line }}</p>
      </div>

      <div class="metrics">
        <div>
          <span>Caller perimeter</span>
          <strong class="number">{{ d.caller.perimeter }}</strong>
          <small>{{ d.caller.perimeter_level }} level</small>
        </div>
        <div>
          <span>Perimeter model</span>
          <strong class="number">{{ d.perimeter_model.levels }} levels</strong>
          <small>{{ d.perimeter_model.shape }}, siblings never merge</small>
        </div>
        <div>
          <span>Physical store isolation</span>
          <strong class="number">{{ d.storage.per_perimeter_physical_stores ? 'PER PERIMETER' : 'SHARED' }}</strong>
          <small>{{ d.storage.in_memory ? 'in-memory' : 'on disk' }}</small>
        </div>
        <div>
          <span>Agentic layer</span>
          <strong class="number">{{ d.agentic_layer.enabled ? 'ON' : 'OFF' }}</strong>
          <small>{{ d.agentic_layer.severable ? 'severable' : 'not severable' }}</small>
        </div>
      </div>

      <div class="posture-grid">
        <div class="panel">
          <div class="panel-title"><h2>Capsules</h2><span>§9.2</span></div>
          <div class="capsule outer">
            <span class="eyebrow">Outer — org</span>
            <p>{{ d.perimeter_model.outer }}</p>
            <div class="capsule inner">
              <span class="eyebrow">Inner — operator</span>
              <p>{{ d.perimeter_model.inner }}</p>
            </div>
          </div>
          <p class="posture-note">{{ d.crossing_a_boundary }}</p>
        </div>

        <div class="panel">
          <div class="panel-title"><h2>Storage</h2><span>§9.4</span></div>
          <dl class="posture-dl">
            <dt>Store for this caller</dt>
            <dd class="number">{{ d.storage.store_for_caller }}</dd>
            <dt>Storage root</dt>
            <dd class="number">{{ d.storage.storage_root ?? 'in-memory' }}</dd>
          </dl>
          <p class="posture-note">{{ d.storage.isolation_mechanism }}</p>
          <p class="posture-note">{{ d.storage.second_mechanism }}</p>
        </div>
      </div>

      <!-- §9.7: mode, endpoint location, and the resolved address in one row so
           a reviewer can cross-check them at a glance. Rendered only when the
           backend actually sends the classifier fields; older backends omit
           this panel entirely rather than showing empty cells. -->
      <div class="panel compact" v-if="d.agentic_layer.effective_mode">
        <div class="panel-title"><h2>Agentic mode &amp; endpoint</h2><span>§9.7</span></div>
        <dl class="posture-dl posture-dl-grid">
          <div>
            <dt>Declared mode</dt>
            <dd class="number">{{ d.agentic_layer.declared_mode ?? 'not declared' }}</dd>
          </div>
          <div>
            <dt>Effective mode</dt>
            <dd class="number">{{ d.agentic_layer.effective_mode }}</dd>
          </div>
          <div>
            <dt>Endpoint location</dt>
            <dd class="number">{{ d.agentic_layer.endpoint_location }}</dd>
          </div>
          <div>
            <dt>Endpoint host</dt>
            <dd class="number">{{ d.agentic_layer.endpoint_host ?? '(none)' }}</dd>
          </div>
          <div>
            <dt>Resolved address</dt>
            <dd class="number">{{ d.agentic_layer.resolved_address ?? '(unresolved)' }}</dd>
          </div>
          <div v-if="d.agentic_layer.inside_perimeter_override_applied">
            <dt>Inside-perimeter override</dt>
            <dd class="number">applied via ESP_LLM_INSIDE_PERIMETER</dd>
          </div>
        </dl>
        <p class="posture-note">
          §9.7 says the deployment posture must report where the endpoint actually points, not
          which mode is nominally selected. The resolved address is the fact; the declared mode is
          the intent. When they disagree, the disagreement is surfaced at the top of this page.
        </p>
        <details v-if="d.agentic_layer.config_env_surface && d.agentic_layer.config_env_surface.length">
          <summary>Configuration surface (env vars that change this posture)</summary>
          <dl class="posture-dl">
            <template v-for="row in d.agentic_layer.config_env_surface" :key="row.name">
              <dt>{{ row.name }}</dt>
              <dd>{{ row.purpose }}</dd>
            </template>
          </dl>
        </details>
      </div>

      <div class="panel compact">
        <div class="panel-title"><h2>Egress channels</h2><span>§9.1 / §9.7</span></div>
        <p v-if="!d.egress_channels.length" class="posture-note">
          No egress channel exists in this deployment. {{ d.agentic_layer.note }}
        </p>
        <div
          v-for="c in d.egress_channels"
          :key="c.channel"
          class="egress"
          :class="c.crosses_perimeter === false ? 'egress-local' : ''"
        >
          <div class="egress-head">
            <strong class="number">{{ c.channel }}</strong>
            <span class="number">{{ c.endpoint }}</span>
          </div>
          <p v-if="c.endpoint_location" class="egress-meta">
            <span>host: <code>{{ c.endpoint_host ?? '(none)' }}</code></span>
            <span>resolved: <code>{{ c.resolved_address ?? '(unresolved)' }}</code></span>
            <span>location: <code>{{ c.endpoint_location }}</code></span>
            <span>crosses perimeter: <code>{{ c.crosses_perimeter ? 'yes' : 'no' }}</code></span>
          </p>
          <p>{{ c.what_is_sent }}</p>
          <p>{{ c.consequence }}</p>
        </div>
      </div>

      <div class="panel compact" v-if="d.browser_egress">
        <div class="panel-title"><h2>Browser egress</h2><span>§9.7</span></div>
        <dl class="posture-dl">
          <div>
            <dt>Third-party requests</dt>
            <dd class="number">{{ d.browser_egress.third_party_requests ? 'YES' : 'NONE' }}</dd>
          </div>
          <div>
            <dt>Typefaces</dt>
            <dd class="number">{{ d.browser_egress.fonts }}</dd>
          </div>
          <div>
            <dt>Analytics / error reporting</dt>
            <dd class="number">{{ d.browser_egress.analytics_or_error_reporting ?? 'none' }}</dd>
          </div>
          <div>
            <dt>Verified by</dt>
            <dd class="number">{{ d.browser_egress.verified_by }}</dd>
          </div>
        </dl>
        <p class="posture-caveat">{{ d.browser_egress.enforcement }}</p>
      </div>

      <div class="panel compact">
        <div class="panel-title"><h2>Vendor access</h2><span>§9.1</span></div>
        <p class="posture-note">{{ d.vendor_access }}</p>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* Facts stay plain and monospace; the amber treatment marks something the reader
   must know about, matching how assumptions are styled elsewhere. It is not the
   danger treatment, which is reserved for blocking hard stops. */
.posture-lead{display:grid;gap:var(--s3);padding:var(--s5);border:1px solid var(--line);border-left:6px solid var(--accent);background:var(--surface);border-radius:var(--radius);margin-bottom:var(--s5)}
.posture-lead.has-egress{border-color:color-mix(in srgb,var(--amber) 60%,var(--line));border-left-color:var(--amber);background:var(--amber-soft)}
.posture-lead strong{display:block;font-size:var(--text-xl);line-height:1.1;margin-top:5px}
.posture-lead.has-egress strong{color:var(--amber)}
.posture-lead p{color:var(--text);max-width:88ch}
.posture-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--s4);margin-top:var(--s4)}
.capsule{border:1px solid var(--line);border-radius:var(--radius);padding:var(--s4);background:var(--surface2)}
.capsule.inner{margin-top:var(--s3);background:var(--surface);border-style:dashed}
.capsule p{color:var(--text);font-size:13px;margin-top:5px}
.posture-note{margin-top:var(--s3);font-size:13px;line-height:1.55;color:var(--muted)}
.posture-dl{display:grid;gap:var(--s1);margin:0;min-width:0}
.posture-dl dt{font:600 11px var(--font-mono);text-transform:uppercase;letter-spacing:.04em;color:var(--faint)}
.posture-dl dd{margin:0 0 var(--s3);font-size:12px;line-height:1.5;overflow-wrap:anywhere;word-break:break-all;min-width:0}
.egress{border:1px solid color-mix(in srgb,var(--amber) 55%,var(--line));border-left:5px solid var(--amber);background:var(--amber-soft);padding:var(--s4);border-radius:3px}
.egress+.egress{margin-top:var(--s3)}
.egress-head{display:flex;gap:var(--s3);justify-content:space-between;align-items:baseline;flex-wrap:wrap;margin-bottom:var(--s2)}
.egress-head strong{font-size:var(--text-sm);color:var(--amber);text-transform:uppercase}
.egress-head span{font-size:12px;color:var(--text)}
.egress p{color:var(--text);font-size:13px}
.egress p+p{margin-top:6px}
/* The shared .metrics strip is a fixed four-column grid whose monospace values
   carry white-space:nowrap, so at narrow widths its min-content width pushed the
   page into horizontal scroll. These values are allowed to wrap instead. */
.metrics{min-width:0}
.metrics>div{min-width:0}
.metrics .number{white-space:normal;overflow-wrap:anywhere}
/* Global .number is nowrap for tabular figures. Store URLs and endpoints are long
   identifiers rather than figures, so they wrap inside their panel. */
.posture-dl .number,.egress-head .number{white-space:normal;overflow-wrap:anywhere;word-break:break-all}
/* Amber, not danger red: the enforcement caveat is a qualification the reader
   must not miss, but it is not a blocking hard stop. */
.posture-caveat{margin-top:var(--s4);padding:var(--s3) var(--s4);background:var(--amber-soft);border-left:4px solid var(--amber);color:var(--text);font-size:12px;line-height:1.5}
/* §9.7 contradiction: the one danger-red block on the page. Reserved for a
   real disagreement between the declared mode and what the deployment is
   doing; a mere caveat gets amber, never this treatment. */
.posture-contradiction{display:grid;gap:var(--s2);padding:var(--s4) var(--s5);border:1px solid color-mix(in srgb,var(--danger) 55%,var(--line));border-left:6px solid var(--danger);background:var(--danger-soft);border-radius:var(--radius);margin-bottom:var(--s4);color:var(--text)}
.posture-contradiction strong{display:block;font-size:var(--text-lg);color:var(--danger);margin-top:4px}
.posture-contradiction .pc-label{display:inline-block;font:600 10px var(--font-mono);text-transform:uppercase;letter-spacing:.06em;color:var(--danger);margin-right:6px}
.posture-contradiction code{font-family:var(--font-mono);font-size:12px;background:var(--surface);padding:1px 6px;border-radius:2px;border:1px solid var(--line);margin-right:var(--s3)}
.posture-contradiction .pc-reason{color:var(--text);font-size:12px;line-height:1.5}
/* §9.7 mode/endpoint grid: dl in a responsive grid so the six facts land on one screen. */
.posture-dl-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--s3) var(--s5)}
.posture-dl-grid>div{min-width:0}
/* An inside-perimeter egress channel is not a hazard; only the outside path is
   amber. The local variant keeps the shape of the amber card so the layout is
   consistent, but drops the alarm colour. */
.egress.egress-local{border-color:color-mix(in srgb,var(--accent) 40%,var(--line));border-left-color:var(--accent);background:var(--surface2)}
.egress.egress-local .egress-head strong{color:var(--accent)}
.egress-meta{display:flex;flex-wrap:wrap;gap:var(--s3);font:11px var(--font-mono);color:var(--muted)}
.egress-meta code{background:var(--surface);padding:1px 4px;border-radius:2px}
details{margin-top:var(--s3);font-size:12px}
details summary{cursor:pointer;font:600 11px var(--font-mono);text-transform:uppercase;letter-spacing:.04em;color:var(--muted);padding:var(--s2) 0}
@media(max-width:900px){.posture-grid{grid-template-columns:1fr}.posture-dl-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.metrics>div:nth-child(2n){border-right:0}.metrics>div:nth-child(-n+2){border-bottom:1px solid var(--line)}}
@media(max-width:560px){.metrics{grid-template-columns:1fr}.posture-dl-grid{grid-template-columns:1fr}.metrics>div{border-right:0;border-bottom:1px solid var(--line)}.metrics>div:last-child{border-bottom:0}.posture-lead strong{font-size:var(--text-lg)}}
</style>
