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

// Comparação local MongoDB × PostgreSQL (queries/compare_postgres.py). Opcional:
// sem arquivo válido, a seção mostra o protocolo como pendente.
const latency = l => l && ['p50_ms','p95_ms'].every(k => finite(l[k])) && finite(l.n)
export function validComparisonReport(d) {
  return Boolean(d && d.schema_version === 1 && d.status === 'measured_locally' &&
    Number.isFinite(Date.parse(d.measured_at)) && typeof d.environment === 'string' &&
    typeof d.engines?.mongodb === 'string' && typeof d.engines?.postgresql === 'string' &&
    Array.isArray(d.method?.concurrency) && d.method.concurrency.length && d.method.concurrency.every(finite) &&
    finite(d.method?.runs_per_cell) && d.method.runs_per_cell > 0 &&
    Array.isArray(d.not_measured) && d.not_measured.every(t => typeof t === 'string') && typeof d.passed === 'boolean' &&
    Array.isArray(d.cells) && d.cells.length && d.cells.every(c => c && typeof c.cell === 'string' &&
      typeof c.same_result === 'boolean' && finite(c.fixture_companies) && finite(c.returned_companies) &&
      Array.isArray(c.reasons) &&
      (!c.same_result || d.method.concurrency.every(n => latency(c.latency?.[String(n)]?.mongodb) && latency(c.latency?.[String(n)]?.postgresql)))))
}

// Texto da carga da máquina durante a rodada. Sem `end`, omite o valor final.
// O limiar vem do relatório (metade dos núcleos da máquina medida); sem ele, 2.
const IDLE_LOAD = 2
const load = n => new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 1 }).format(n)
export function loadNote(d) {
  const start = d?.host_load_average?.start?.[0]
  if (!Number.isFinite(start)) return null
  const end = d.host_load_average.end?.[0]
  const hasEnd = Number.isFinite(end)
  const t = d.host_load_average.idle_threshold
  const limit = Number.isFinite(t) && t > 0 ? t : IDLE_LOAD
  const busy = start >= limit || (hasEnd && end >= limit)
  const values = hasEnd ? `${load(start)} → ${load(end)}` : `${load(start)} no início`
  return {
    busy,
    text: busy
      ? `Medição preliminar sob carga (load average de 1 min: ${values}), com outros processos na mesma máquina; as caudas com 8 clientes são as mais afetadas.`
      : `Máquina ociosa durante a rodada (load average de 1 min: ${values}, abaixo de ${load(limit)}).`,
  }
}
