import { useEffect, useState } from 'react'
import './validation.css'
import { validEvidenceReport } from './evidenceReport'

const number = n => new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 }).format(n)
const money = cents => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(cents / 100)
const repo = 'https://github.com/adrianofratelli-glitch/atlas-graph-economic-group'

function Curve({ rows }) {
  const regular = rows.filter(r => r.branching <= 3)
  const ceiling = Math.max(1, ...regular.map(r => r.p95_ms)) * 1.15
  const x = depth => 64 + (depth - 1) * 108
  const y = ms => 225 - ms / ceiling * 185
  return <figure className="validation-chart">
    <svg viewBox="0 0 650 275" role="img" aria-labelledby="curve-title curve-desc">
      <title id="curve-title">Latência p95 por profundidade e ramificação</title>
      <desc id="curve-desc">Uma linha para cada número de filhos por empresa. Valores exatos e quantidade de vínculos na tabela seguinte.</desc>
      {[0,.5,1].map(r => <g key={r}><line x1="64" x2="606" y1={y(r*ceiling)} y2={y(r*ceiling)} stroke="#2a424d"/><text x="56" y={y(r*ceiling)+4} textAnchor="end">{number(r*ceiling)}</text></g>)}
      {[1,2,4,6].map(d => <text key={d} x={x(d)} y="247" textAnchor="middle">{d}</text>)}
      <text x="64" y="18">p95 · ms, incluindo rede</text><text x="335" y="270" textAnchor="middle">Níveis societários</text>
      {[1,2,3].map((branch,i) => {
        const points = regular.filter(r => r.branching === branch)
        const color = ['#00ed64','#49b6ff','#ffad00'][i]
        return <g key={branch}><polyline points={points.map(r => `${x(r.depth)},${y(r.p95_ms)}`).join(' ')} fill="none" stroke={color} strokeWidth="2.5"/>{points.map(r => <circle key={r.depth} cx={x(r.depth)} cy={y(r.p95_ms)} r="4" fill={color}><title>{branch} filhos, {r.depth} níveis: {number(r.p95_ms)} ms</title></circle>)}</g>
      })}
    </svg>
    <figcaption><span style={{color:'#00ed64'}}>● 1 filho</span><span style={{color:'#49b6ff'}}>● 2 filhos</span><span style={{color:'#ffad00'}}>● 3 filhos</span></figcaption>
  </figure>
}

export default function ValidationPage() {
  const [data,setData] = useState(null)
  const [error,setError] = useState(false)
  const [selected,setSelected] = useState(0)
  const [attempt,setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 15000)
    setError(false)
    fetch('/evidence/results.json', { signal: controller.signal }).then(async r => {
      if (!r.ok) throw new Error('unavailable')
      const d = await r.json()
      if (!validEvidenceReport(d)) throw new Error('invalid')
      if (active) { setData(d); setError(false) }
    }).catch(e => { if (active) setError(true) }).finally(() => clearTimeout(timer))
    return () => { active = false; clearTimeout(timer); controller.abort() }
  },[attempt])
  const audit = data?.audit[selected]
  return <div className="validation-page">
    <a className="validation-back" href="/">← Voltar à investigação</a>
    <header className="validation-heading"><p className="validation-eyebrow">Grupo econômico · evidências técnicas</p><h1>O resultado precisa fechar.<br/><span>O limite precisa aparecer.</span></h1><p>Composição conferida, exposição reconciliada e custo observado à medida que a árvore cresce.</p></header>
    {error ? <div role="alert" className="validation-card"><h2>Não foi possível carregar as medições</h2><p>Confira a conexão e tente novamente. A investigação continua disponível.</p><button className="btn" onClick={() => setAttempt(v=>v+1)}>Tentar novamente</button></div> : !data ? <p role="status">Carregando evidências…</p> : <>
      <div className="validation-meta"><span>Fotografia de {new Date(data.measured_at).toLocaleString('pt-BR')}</span><span>MongoDB {data.mongodb_version}</span><a href="/evidence/results.json" download="graph-evidence.json">Baixar dados e planos</a></div>
      <p className="validation-note">{data.environment}. Os resultados abaixo são uma medição publicada, não o estado atual do cluster.</p>
      <nav className="validation-nav" aria-label="Seções de evidências"><a href="#audit">01 · Conferir o resultado</a><a href="#curve">02 · Entender o limite</a><a href="#comparison">03 · Comparar alternativas</a></nav>
      <section id="audit" className="validation-card">
        <div className="validation-section-title"><div><p className="validation-eyebrow">01 / Investigação auditável</p><h2>Da empresa à soma do grupo</h2></div><label>Grupo de referência<select value={selected} onChange={e=>setSelected(Number(e.target.value))}>{data.audit.map((a,i)=><option key={a.group_id} value={i}>{a.levels} níveis · {a.expected_company_ids.length} empresas</option>)}</select></label></div>
        <p>{audit.applicant} · CNPJ {audit.cnpj}</p>
        <div className="validation-facts"><article><span>Esperado pelo manifesto</span><strong>{audit.expected_company_ids.length} empresas</strong></article><article><span>Encontrado pelo traversal</span><strong>{audit.observed_company_ids.length} empresas</strong></article><article><span>Conferência independente</span><strong className={Object.values(audit.checks).every(Boolean)?'validation-ok':'validation-fail'}>{Object.values(audit.checks).every(Boolean)?'Conferido':'Divergência encontrada'}</strong></article></div>
        <div className="validation-table-wrap"><table><caption>Exposição em leituras diretas versus resposta do grupo</caption><thead><tr><th>Valor</th><th>Leitura independente</th><th>Traversal</th><th>Diferença</th></tr></thead><tbody>{[['limite','Limite'],['utilizado','Utilizado'],['vencido','Vencido']].map(([key,label])=><tr key={key}><th>{label}</th><td>{money(audit.expected_cents[key])}</td><td>{money(audit.observed_cents[key])}</td><td>{money(audit.observed_cents[key]-audit.expected_cents[key])}</td></tr>)}</tbody></table></div>
        <h3>Vínculos entre a solicitante e uma empresa em atraso</h3><ol className="validation-path">{audit.path.map(n=><li key={n.id}>{n.name}</li>)}</ol><p className="validation-note">Caminho pelas participações corporativas, percorrido nos dois sentidos para explicar a ligação. Não representa fluxo financeiro nem responsabilidade solidária.</p>
        <details><summary>Conferir as {audit.members.length} empresas e seus valores</summary><div className="validation-table-wrap"><table><thead><tr><th>Empresa</th><th>Limite</th><th>Utilizado</th><th>Vencido</th></tr></thead><tbody>{audit.members.map(m=><tr key={m.id}><th>{m.name}</th><td>{money(m.limite)}</td><td>{money(m.utilizado)}</td><td>{money(m.vencido)}</td></tr>)}</tbody></table></div></details>
        <details><summary>Verificação e plano de execução</summary><p>Referência: manifesto economic_groups, arestas corporativas internas e exposições lidas diretamente. Valores comparados em centavos. A consulta de referência usa o limite de seis níveis.</p><p>SHA-256 das fontes: <code className="validation-hash">{audit.source_sha256}</code></p><p>Contadores reportados pelo servidor, preservados por estágio. Não somamos contadores de pais e filhos; ausência de uma métrica não significa zero.</p><pre>{JSON.stringify({checks:audit.checks, traversal_matches:audit.traversal_matches, executionStats:audit.explain}, null, 2)}</pre></details>
      </section>
      <section id="curve" className="validation-card"><p className="validation-eyebrow">02 / Curva de comportamento</p><h2>Profundidade sozinha não explica o custo</h2><p>Aumentamos níveis e filhos por empresa em uma base temporária no mesmo Atlas. Cada ponto tem {data.runs} execuções após aquecimento, sem concorrência. Ping p50: {number(data.ping.p50_ms)} ms.</p><Curve rows={data.curve}/><div className="validation-table-wrap"><table><caption>Resultados medidos · p95 pelo método nearest rank</caption><thead><tr><th>Níveis</th><th>Filhos</th><th>Vínculos na árvore</th><th>Subida / descida</th><th>Vínculos exibidos</th><th>p50 ms</th><th>p95 ms</th><th>Alcance</th></tr></thead><tbody>{data.curve.map(r=><tr key={`${r.depth}-${r.branching}`}><td>{r.depth}</td><td>{number(r.branching)}</td><td>{number(r.fixture_edges)}</td><td>{r.traversal_matches ? `${number(r.traversal_matches.up)} / ${number(r.traversal_matches.down)}` : "—"}</td><td>{number(r.returned_edges)}</td><td>{number(r.p50_ms)}</td><td>{number(r.p95_ms)}</td><td>{!r.correct?'Divergente':r.complete?'Completo':'Parcial · revisão bloqueada'}</td></tr>)}</tbody></table></div><p className="validation-note">Subida e descida contam correspondências de arestas em cada direção; a mesma aresta pode aparecer nas duas. O número de vínculos da árvore é conhecido pelo gerador; vínculos exibidos são o payload final, não um contador de documentos examinados. A base temporária não contém as {number(data.production_counts.companies)} empresas da demonstração. Planos por estágio e amostras individuais estão no download. Não subtraímos ping dos percentis.</p><p>O caso com 1.200 filhos expõe o teto de apresentação. Ele não determina o limite físico do MongoDB. Não houve busca pelo ponto de exaustão do cluster compartilhado.</p></section>
      <section id="comparison" className="validation-card"><p className="validation-eyebrow">03 / Comparação entre alternativas</p><h2>PostgreSQL: medição pendente</h2><p>A comparação com uma consulta recursiva foi adiada até haver um ambiente equivalente. Não há vencedor declarado.</p><ul><li>Mesmo conjunto de empresas, participações, créditos e resultados esperados.</li><li>Mesmos recursos, proximidade de rede, concorrência e política de aquecimento.</li><li>Separar execução no servidor, transferência, carga inicial e custo operacional.</li></ul><a href={`${repo}/blob/main/docs/comparison-protocol.md`}>Ler o protocolo de comparação →</a></section>
      <footer className="validation-note">Dados sintéticos. A auditoria demonstra correção frente ao manifesto, não precisão de identificação em um cadastro real. <a href={`${repo}/blob/main/docs/resilience-validation.md`}>Testes de resiliência e limites</a></footer>
    </>}
  </div>
}
