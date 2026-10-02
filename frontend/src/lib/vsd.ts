import type { Candidate } from './types'

// Old persisted designs have no assessment/evidence. Never infer recovery
// from their legacy extended_validity_months field.
export function verifiedRecovery(candidate: Candidate) {
  const r = candidate.vsd_recovery
  if (r?.assessment !== 'verified' || !r.verified_points?.length) return []
  return r.verified_points.filter(p =>
    Number.isFinite(p.head_developed_ft) && Number.isFinite(p.head_required_ft)
    && p.head_developed_ft >= p.head_required_ft - 1e-6
    && (r.frequency_schedule ?? []).some(([m, f]) => m === p.month && f === p.frequency_hz)
  )
}
