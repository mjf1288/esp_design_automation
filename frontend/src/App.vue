<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useEspStore } from "./stores";
import IntakeView from "./views/IntakeView.vue";
import CaseReviewView from "./views/CaseReviewView.vue";
import ResultsView from "./views/ResultsView.vue";
import TimelineView from "./views/TimelineView.vue";
import EmpiricalView from "./views/EmpiricalView.vue";
import PostureView from "./views/PostureView.vue";
import EngineeringView from "./views/EngineeringView.vue";
import DemoCaseBar from "./components/DemoCaseBar.vue";
const store = useEspStore();
const dark = ref(document.documentElement.dataset.theme !== "light");
const themeKey = ref(0);
const views = [
  ["intake", "Intake"],
  ["case", "Case review"],
  ["results", "Design results"],
  ["engineering", "Engineering decisions"],
  ["timeline", "Validity timeline"],
  ["empirical", "Empirical"],
  ["posture", "Deployment posture"],
] as const;
const current = computed(
  () =>
    ({
      intake: IntakeView,
      case: CaseReviewView,
      results: ResultsView,
      engineering: EngineeringView,
      timeline: TimelineView,
      empirical: EmpiricalView,
      posture: PostureView,
    })[store.activeView] ?? ResultsView,
);
// Views that present a design result are gated on one existing; views that describe
// the deployment or the case itself are not.
const DESIGN_DEPENDENT = ["results", "engineering", "timeline", "empirical"];
const designGated = computed(() => DESIGN_DEPENDENT.includes(store.activeView));
function theme() {
  dark.value = !dark.value;
  document.documentElement.dataset.theme = dark.value ? "dark" : "light";
  themeKey.value++;
}
onMounted(() => {
  store.load();
  store.loadDeployment();
});
</script>
<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <svg aria-label="ESP Design Automation" viewBox="0 0 48 48" fill="none">
          <path
            d="M24 4 40 13v22L24 44 8 35V13L24 4Z"
            stroke="currentColor"
            stroke-width="3"
          />
          <path d="m17 16 14 8-14 8V16Z" fill="currentColor" />
          <path d="M31 24h7" stroke="var(--accent)" stroke-width="3" />
        </svg>
        <div><strong>ESP</strong><span>DESIGN AUTOMATION</span></div>
      </div>
      <nav aria-label="Primary views">
        <button
          v-for="[id, label] in views"
          :key="id"
          :class="{ active: store.activeView === id }"
          @click="store.activeView = id"
          :data-testid="`nav-${id}`"
        >
          <span class="nav-index"
            >0{{ views.findIndex((x) => x[0] === id) + 1 }}</span
          >{{ label }}
        </button>
      </nav>
      <div class="sidebar-foot">
        <p>FACTS / JUDGMENTS<br />structurally separated</p>
        <p class="number" v-if="store.deployment">
          {{ store.deployment.caller.perimeter }}<br />{{
            store.deployment.agentic_layer.enabled
              ? "agentic on"
              : "agentic off"
          }}
        </p>
        <p class="number">
          engine {{ store.design.provenance.engine_version }}
        </p>
      </div>
    </aside>
    <main :class="{'synthetic-scope': (store.caseData as any).synthetic}">
      <header class="topbar">
        <div>
          <span class="well">{{
            store.caseData.metadata?.well_name ?? "No well name"
          }}</span
          ><span class="muted">
            / {{ store.caseData.metadata?.field_name ?? "field absent" }}</span
          >
        </div>
        <div class="top-actions">
          <span
            v-if="store.job && store.job.facts_ready && !store.job.terminal"
            class="status-pill run-pill"
            data-testid="pill-narrative-pending"
            title="Facts are final. The narrative is still being generated and will attach to this design."
            ><template v-if="store.job.cancel_requested"
              >cancelling narrative · waiting on in-flight model call</template
            ><template v-else
              >narrative pending ·
              <span class="number">{{ store.job.elapsed_s.toFixed(0) }} s</span>
              <button
                class="text-button"
                data-testid="button-cancel-narrative"
                @click="store.cancelJob()"
              >
                cancel
              </button></template
            ></span
          ><span
            v-else-if="
              store.job &&
              store.job.terminal &&
              store.job.design_id &&
              store.job.narrative_status &&
              !['ok', 'disabled'].includes(store.job.narrative_status)
            "
            class="status-pill run-pill warn"
            data-testid="pill-narrative-status"
            :title="store.jobError || 'Facts are unaffected.'"
            >narrative {{ store.job.narrative_status }}</span
          ><span
            v-if="store.usingFixture"
            class="fixture-badge"
            title="API unavailable: schema-aligned fixture is shown for preview"
            >fixture preview</span
          ><button
            class="theme"
            @click="theme"
            data-testid="button-theme"
            :aria-label="
              dark ? 'Switch to light theme' : 'Switch to dark theme'
            "
          >
            {{ dark ? "☼" : "◐" }}
          </button>
        </div>
      </header>
      <DemoCaseBar/>
      <component
        v-if="!designGated"
        :is="current"
        :key="`${store.activeView}-${themeKey}`"
      />
      <div v-else-if="store.designState === 'loading'" class="loading-view">
        <i /><i /><i /><i />
      </div>
      <div
        v-else-if="store.designState === 'running'"
        class="error-state neutral"
        data-testid="run-queued"
      >
        <strong>{{
          store.job?.status === "queued"
            ? "Design run queued"
            : "Enumerating configurations"
        }}</strong>
        <p>
          The engine is evaluating every candidate configuration across the
          forecast horizon. Facts appear as soon as the deterministic stage
          stores them, usually within a second of starting; the narrative
          follows separately.
        </p>
        <p class="number">
          {{ store.job?.status ?? "submitting" }} ·
          {{ (store.job?.elapsed_s ?? 0).toFixed(1) }} s
        </p>
        <button
          v-if="store.job && !store.job.terminal && !store.job.cancel_requested"
          class="primary"
          data-testid="button-cancel-run"
          @click="store.cancelJob()"
        >
          Cancel run
        </button>
      </div>
      <div
        v-else-if="store.designState === 'no-run'"
        class="error-state neutral"
      >
        <strong>No design run for this case</strong>
        <p>
          This case has no stored design yet. Running the engine enumerates
          every candidate configuration and takes a few minutes. The
          deterministic facts are ready in about a second; the narrative is
          generated afterwards and can take minutes. The result is immutable and
          reproducible, so it is computed once and then reused.
        </p>
        <button class="primary" @click="store.runDesign()">
          Run design engine
        </button>
      </div>
      <div v-else-if="store.designState === 'error'" class="error-state">
        <strong>Service response unavailable</strong>
        <p>{{ store.apiError }}</p>
        <button class="primary" @click="store.load()">Retry service</button>
      </div>
      <component
        v-else
        :is="current"
        :key="`${store.activeView}-${themeKey}`"
      />
    </main>
  </div>
</template>
