const finite = n => typeof n === 'number' && Number.isFinite(n) && n >= 0
const exposure = v => v && ['limite','utilizado','vencido'].every(k => finite(v[k]))
export function validEvidenceReport(d) {
  return Boolean(d && d.schema_version === 1 && Number.isFinite(Date.parse(d.measured_at)) &&
    typeof d.environment === 'string' && typeof d.mongodb_version === 'string' && finite(d.runs) &&
    finite(d.ping?.p50_ms) && finite(d.production_counts?.companies) &&
    Array.isArray(d.audit) && d.audit.length && d.audit.every(a => a &&
      typeof a.group_id === 'string' && typeof a.applicant === 'string' && typeof a.cnpj === 'string' && finite(a.levels) &&
      Array.isArray(a.expected_company_ids) && Array.isArray(a.observed_company_ids) &&
      exposure(a.expected_cents) && exposure(a.observed_cents) &&
      a.checks && typeof a.checks === 'object' && Object.keys(a.checks).length > 0 && Object.values(a.checks).every(v => typeof v === 'boolean') &&
      Array.isArray(a.members) && a.members.every(m => m && typeof m.id === 'string' && typeof m.name === 'string' && exposure(m)) &&
      Array.isArray(a.path) && a.path.every(p => p && typeof p.id === 'string' && typeof p.name === 'string')) &&
    Array.isArray(d.curve) && d.curve.length && d.curve.every(r => r &&
      ['depth','branching','fixture_edges','returned_edges','p50_ms','p95_ms'].every(k => finite(r[k])) &&
      typeof r.correct === 'boolean' && typeof r.complete === 'boolean'))
}
