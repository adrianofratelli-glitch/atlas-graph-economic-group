import { useCallback, useEffect, useMemo, useState, useRef } from 'react'
import EvidencePanel from './EvidencePanel'
import GraphCanvas from './GraphCanvas'
import QueryDetails from './QueryDetails'
import { api } from './api'

const DEPTHS = [1, 2, 3, 4, 5, 6]
const TABS = [
  ['evidence', 'Resumo'],
  ['company', 'Empresa'],
  ['search', 'Busca'],
  ['semantic', 'Concentração'],
  ['visibility', 'Visibilidade'],
  ['alerts', 'Alertas'],
]

const fmt = (n) => (n == null ? '—' : n.toLocaleString('pt-BR'))
const money = (n, cur = 'R$') => `${cur} ${fmt(Math.round(n ?? 0))}`
// Valor compacto para a barra de métricas: numa tela de projetor "96M" é lido de
// relance e "96,326,588" não é. O número exato fica no cartão.
const compact = (n) =>
  n == null ? '—' : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : Math.round(n)

export default function App() {
  const groupRequest = useRef(0)
  const searchRequest = useRef(0)
  const mutationLock = useRef(false)
  const [writing, setWriting] = useState(false)
  const [startupError, setStartupError] = useState(null)
  const [startupVersion, setStartupVersion] = useState(0)
  const [streamState, setStreamState] = useState('conectando')
  const [health, setHealth] = useState(null)
  const [entry, setEntry] = useState(null)
  const [cnpj, setCnpj] = useState('')
  const [depth, setDepth] = useState(3)
  const [group, setGroup] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [caseInfo, setCaseInfo] = useState(null)
  const [caseDetail, setCaseDetail] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [search, setSearch] = useState({ q: '', out: null, degraded: null, todaBase: false })
  const [conc, setConc] = useState({ out: null, degraded: null, saturado: null, loading: false })
  const [tab, setTab] = useState('evidence')
  const [picked, setPicked] = useState(null)
  // O que uma aba lateral está apontando no grafo.
  //
  // Clicar num resultado tinha de fazer o apresentador procurar o nó com o dedo
  // na tela. Agora a lista aponta: o conjunto destacado ganha borda, o resto
  // apaga, e o canvas enquadra o que foi apontado.
  const [destaque, setDestaque] = useState(null)
  // Hierarquia comercial. `vis.user` é quem está olhando; tudo o mais é derivado
  // disso no servidor — a tela nunca monta a lista de contas visíveis.
  const [vis, setVis] = useState({ roster: [], user: null, out: null, check: null })
  const [hovered, setHovered] = useState(null)

  useEffect(() => {
    let active = true
    setStartupError(null)
    api.health().then(h => active && setHealth(h)).catch(() => active && setHealth({ status: 'offline' }))
    api.entryPoints().then(e => {
      if (!active) return
      setEntry(e)
      if (!e.applicants?.length) setStartupError('Nenhum caso preparado está disponível.')
    }).catch(() => active && setStartupError('Não foi possível carregar as empresas. Confira a conexão e tente novamente.'))
    api.roster().then(r => active && setVis(v => ({ ...v, roster: r.users })))
      .catch(() => active && setVis(v => ({ ...v, error: 'Não foi possível carregar os perfis de demonstração.' })))
    return () => { active = false }
  }, [startupVersion])

  useEffect(() => {
    if (!entry?.applicants?.length || cnpj) return
    // A lista vem do raso ao fundo, mas a demo abre no grupo **mais fundo**: é
    // nele que cada passo do controle de profundidade revela empresa nova, e é o
    // passo 3 do roteiro. Quem quiser o caso raso escolhe na lista.
    const maisFundo = entry.applicants.reduce(
      (a, b) => ((b.group_levels ?? 0) > (a.group_levels ?? 0) ? b : a),
      entry.applicants[0]
    )
    setCnpj(maisFundo.cnpj)
  }, [entry, cnpj])

  // Destaque é sempre relativo ao grafo que está na tela: trocar de empresa, de
  // profundidade ou de aba desfaz o apontamento em vez de deixá-lo mentindo.
  useEffect(() => setDestaque(null), [cnpj, depth, tab])

  useEffect(() => {
    if (!vis.user) return
    let vivo = true
    setVis(v => ({ ...v, out: null, saturado: null, error: null, loading: true }))
    api
      .portfolio(vis.user)
      .then((out) => vivo && setVis((v) => ({ ...v, out, saturado: null, loading: false })))
      .catch((err) => vivo && setVis((v) => ({ ...v, out: null, loading: false, error: err.message, saturado: err.saturado ?? null })))
    return () => {
      vivo = false
    }
  }, [vis.user])

  useEffect(() => {
    if (!vis.user || !cnpj) return
    let vivo = true
    setVis(v => ({ ...v, check: null }))
    api
      .canSee(vis.user, cnpj)
      .then((check) => vivo && setVis((v) => ({ ...v, check })))
      .catch(() => vivo && setVis((v) => ({ ...v, check: null })))
    return () => {
      vivo = false
    }
  }, [vis.user, cnpj])

  useEffect(() => {
    const es = new EventSource('/api/alerts/stream')
    es.onopen = () => setStreamState('conectado')
    es.onerror = () => setStreamState('reconectando')
    es.onmessage = (ev) => {
      let evento
      try { evento = JSON.parse(ev.data) } catch { setStreamState('evento inválido'); return }
      if (!['review_opened', 'review_closed'].includes(evento.type) || !evento.case_id) return
      setAlerts((prev) => [evento, ...prev.filter(a => a.case_id !== evento.case_id || a.type !== evento.type)].slice(0, 20))
      // Abrir revisão troca a aba: é o evento que o apresentador quer ver
      // chegando sozinho. O encerramento fica na lista sem tirá-lo da tela.
      // Eventos de outros grupos não mudam a aba nem o contexto da investigação.
    }
    return () => es.close()
  }, [])

  const solicitanteNiveis =
    (entry?.applicants ?? []).find((a) => a.cnpj === cnpj)?.group_levels ?? null

  // Quais empresas do grupo na tela o usuário selecionado alcança.
  //
  // Não precisa de endpoint novo: a carteira já devolve o escopo (o usuário mais
  // todo mundo abaixo dele) e cada nó do grafo já carrega o assessor responsável.
  // O cruzamento é local, e é ele que transforma a fronteira de visibilidade em
  // algo que se **vê** no grafo em vez de se ler numa frase.
  const escopoIds = useMemo(
    () => new Set(vis.out ? [vis.out.user.id, ...vis.out.team.map((t) => t.id)] : []),
    [vis.out]
  )
  const foraDoEscopo = useMemo(() => {
    if (!vis.out || !group?.nodes) return null
    return new Set(
      group.nodes
        .filter((n) => n.kind === 'company' && !escopoIds.has(n.advisor_id))
        .map((n) => n.id)
    )
  }, [vis.out, group, escopoIds])
  const cobertura = useMemo(() => {
    if (!foraDoEscopo || !group?.nodes) return null
    const empresas = group.nodes.filter((n) => n.kind === 'company')
    return {
      total: empresas.length,
      dentro: empresas.length - foraDoEscopo.size,
      assessores: new Set(empresas.map((n) => n.advisor_id).filter(Boolean)).size,
    }
  }, [foraDoEscopo, group])

  /** Aponta um conjunto de nós no grafo; clicar de novo no mesmo conjunto desfaz. */
  const aponta = useCallback((ids) => {
    const alvo = new Set((ids ?? []).filter(Boolean))
    setDestaque((atual) => {
      if (!alvo.size) return null
      const igual =
        atual && atual.size === alvo.size && [...alvo].every((i) => atual.has(i))
      return igual ? null : alvo
    })
  }, [])

  const carrega = useCallback(async (c, d) => {
    if (!c) return
    const request = ++groupRequest.current
    ++searchRequest.current
    setBusy(true)
    setError(null)
    setGroup(null)
    setPicked(null)
    setSearch(p => ({ ...p, out: null, degraded: null, loading: false }))
    try {
      const next = await api.group(c, d)
      if (request === groupRequest.current) setGroup(next)
    } catch (e) {
      if (request === groupRequest.current) { setError(e.message); setGroup(null) }
    } finally {
      if (request === groupRequest.current) setBusy(false)
    }
  }, [])

  useEffect(() => {
    setCaseInfo(null)
    setCaseDetail(null)
    if (cnpj) carrega(cnpj, depth)
    return () => { ++groupRequest.current; ++searchRequest.current }
  }, [cnpj, depth, carrega])

  const companyIds = useMemo(
    () => (group?.nodes ?? []).filter((n) => n.kind === 'company').map((n) => n.id),
    [group]
  )
  const byId = useMemo(() => new Map((group?.nodes ?? []).map((n) => [n.id, n])), [group])
  const neighbours = useMemo(() => {
    const m = new Map()
    for (const e of group?.edges ?? []) {
      if (!m.has(e.from)) m.set(e.from, new Set())
      if (!m.has(e.to)) m.set(e.to, new Set())
      m.get(e.from).add(e.to)
      m.get(e.to).add(e.from)
    }
    return m
  }, [group])

  // O nó fixado manda no painel; o hover só preenche quando nada está fixado.
  const detail = byId.get(picked ?? hovered)
  const pinned = Boolean(picked && byId.has(picked))
  const stats = group?.stats
  const exposure = group?.group_exposure
  const subject = group?.subject

  // Recupera uma revisão já aberta quando a tela recarrega: o `case_id` viaja
  // junto com a empresa, então a tela se reconcilia com o servidor sozinha.
  useEffect(() => {
    if (caseInfo) return
    const aberta = (group?.nodes ?? []).find((n) => n.credit_status === 'under_review' && n.case_id)
    if (!aberta) return
    let vivo = true
    api
      .caseDetail(aberta.case_id)
      .then((d) => {
        if (!vivo) return
        setCaseDetail(d)
        setCaseInfo({
          case_id: aberta.case_id,
          companies_blocked: d.stats?.total ?? d.companies.length,
          exposures_flagged: d.case.exposures_flagged,
          elapsed_ms: '—',
          read_concern: 'snapshot',
          write_concern: 'majority',
        })
      })
      .catch(() => {})
    return () => {
      vivo = false
    }
  }, [group, caseInfo])

  const doReview = async () => {
    if (!group?.investigation?.review_allowed || mutationLock.current) return
    mutationLock.current = true
    setWriting(true)
    setBusy(true)
    setError(null)
    try {
      const info = await api.openReview(
        group.investigation.token,
        `grupo econômico alcançado em ${depth} níveis a partir do CNPJ ${cnpj}`
      )
      setCaseInfo(info)
      setCaseDetail(await api.caseDetail(info.case_id).catch(() => null))
      await carrega(cnpj, depth)
    } catch (e) {
      if (e.detail?.already_open && e.detail.case_id) {
        const d = await api.caseDetail(e.detail.case_id).catch(() => null)
        setCaseDetail(d)
        setCaseInfo({
          case_id: e.detail.case_id,
          companies_blocked: d?.stats?.total ?? d?.companies.length,
          exposures_flagged: d?.case?.exposures_flagged,
          elapsed_ms: '—',
          read_concern: 'snapshot',
          write_concern: 'majority',
        })
        setError('Já existe uma revisão sobre este grupo. Encerre-a antes de abrir outra.')
      } else {
        setError(e.message)
      }
    } finally {
      mutationLock.current = false
      setWriting(false)
      setBusy(false)
    }
  }

  const doClose = async () => {
    if (!caseInfo?.case_id || mutationLock.current) return
    mutationLock.current = true
    setWriting(true)
    setError(null)
    setBusy(true)
    try {
      await api.closeReview(caseInfo.case_id)
      setCaseInfo(null)
      setCaseDetail(null)
      await carrega(cnpj, depth)
    } catch (e) {
      setError(e.message)
    } finally {
      mutationLock.current = false
      setWriting(false)
      setBusy(false)
    }
  }

  const doReset = async () => {
    if (mutationLock.current) return
    mutationLock.current = true
    setWriting(true)
    setError(null)
    setBusy(true)
    try {
      await api.reset()
      setCaseInfo(null)
      setCaseDetail(null)
      setAlerts([])
      await carrega(cnpj, depth)
    } catch (e) {
      setError(e.message)
    } finally {
      mutationLock.current = false
      setWriting(false)
      setBusy(false)
    }
  }

  const doSearch = async (e) => {
    e?.preventDefault()
    const request = ++searchRequest.current
    setSearch(p => ({ ...p, loading: true, out: null, degraded: null }))
    try {
      const out = await api.searchCompanies(search.q, {
        companyIds,
        nodeIds: (group?.nodes ?? []).map((n) => n.id),
        scopeOnly: !search.todaBase,
      })
      if (request === searchRequest.current) setSearch((p) => ({ ...p, out, degraded: null, loading: false }))
    } catch (err) {
      if (request === searchRequest.current) setSearch((p) => ({ ...p, out: null, loading: false, degraded: err.degraded ?? { status: err.message } }))
    }
  }

  // Análise de concentração: automática, disparada quando o grupo muda. Não há
  // caixa de texto — a pergunta é do grafo, não do apresentador.
  useEffect(() => {
    if (!companyIds.length) {
      setConc({ out: null, degraded: null, saturado: null, loading: false })
      return
    }
    let vivo = true
    setConc({ out: null, degraded: null, saturado: null, loading: true })
    api
      .concentration(companyIds)
      .then((out) => vivo && setConc({ out, degraded: null, saturado: null, loading: false }))
      .catch((err) =>
        vivo &&
          setConc({
            out: null,
            saturado: err.saturado ?? null,
            degraded: err.saturado ? null : err.degraded ?? { status: err.message },
            loading: false,
          })
      )
    return () => {
      vivo = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group])

  const technicalResult = tab === 'semantic' ? conc.out : tab === 'search' ? search.out : tab === 'visibility' ? vis.out : group
  const technical = technicalResult?.query_details
  const idx = health?.checks?.search_index ?? {}
  const searchReady = Object.values(idx).length >= 2 && Object.values(idx).slice(0, 2).every((v) => v === 'READY')
  const vectorReady = Object.values(idx)[2] === 'READY'

  return (
    <div className="app" data-pov-shell>
      <a className="pov-skip-link" href="#conteudo-principal">Pular para o conteúdo</a>

      <header className="topbar">
        <span className="brand"><svg className="mongo-mark" aria-hidden="true" viewBox="0 0 32 32" width="22" height="22"><path d="M16 5c4 5 6 8.4 6 12.1 0 3.6-2.4 6.4-5.2 7.2l-.8 2.7-.8-2.7C12.4 23.5 10 20.7 10 17.1 10 13.4 12 10 16 5z" fill="currentColor" /></svg> Grupo econômico &amp; risco de crédito</span>
        <span className="thesis">
          A empresa parece pequena sozinha. <em>O grupo econômico dela não.</em>
        </span>
        <span className="spacer" />
        <span className="hint">$graphLookup · Atlas Search · ACID · Change Streams</span>
        <a className="evidence-link" href="/evidence">Evidências técnicas</a>
        <HealthBadge health={health} />
      </header>

      <main className="body" id="conteudo-principal" tabIndex={-1}>
        <aside className="rail" aria-label="Controles">
          {(startupError || error) && <div className="block" role="alert"><p className="notice notice-bad">{startupError || error}</p><button className="btn btn-ghost" disabled={busy || writing} onClick={() => { setStartupVersion(v => v + 1); carrega(cnpj, depth) }}>Tentar novamente</button></div>}
          <section className="block">
            <h2>Solicitante de crédito</h2>
            <select disabled={writing} value={cnpj} onChange={(e) => setCnpj(e.target.value)} aria-label="Empresa em análise">
              <optgroup label="Pedidos de crédito">
                {(entry?.applicants ?? []).map((a) => (
                  <option key={a.cnpj} value={a.cnpj}>
                    {a.razao_social}
                    {a.group_levels ? ` — ${a.group_levels} ${a.group_levels === 1 ? 'nível' : 'níveis'}` : ''}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Sem grupo econômico (controle)">
                {(entry?.control ?? []).map((c) => (
                  <option key={c.cnpj} value={c.cnpj}>{c.razao_social}</option>
                ))}
              </optgroup>
            </select>
            {subject && (
              <p className="muted small">
                CNPJ <code>{subject.cnpj}</code> · sozinha: rating {subject.rating ?? '—'} ·
                limite {money(subject.limite, exposure?.currency)} · vencido{' '}
                <b className={subject.vencido > 0 ? 'bad' : ''}>{money(subject.vencido, exposure?.currency)}</b>
              </p>
            )}
          </section>

          <section className="block">
            <h2>Níveis societários</h2>
            <div className="depth-row" role="group" aria-label="Profundidade do traversal">
              {DEPTHS.map((d) => (
                <button key={d} disabled={writing} className="depth-btn" aria-pressed={depth === d} onClick={() => setDepth(d)}>
                  {d}
                </button>
              ))}
            </div>
            <p className="muted small">
              Cada solicitante da lista pertence a um grupo com profundidade diferente
              {solicitanteNiveis ? ` — este tem ${solicitanteNiveis}` : ''}. Subir além do fundo da
              árvore devolve o mesmo grupo: é o traversal terminando, não a consulta falhando. A
              maioria das consultas reais resolve entre 2 e 4.
            </p>
          </section>

          <section className="block block-action">
            <h2>Decisão de crédito</h2>
            {!caseInfo && (
              <button
                className="btn btn-primary btn-block"
                onClick={doReview}
                disabled={busy || writing || !group?.investigation?.review_allowed}
              >
                Abrir revisão sobre {companyIds.length || 0} empresas
              </button>
            )}

            {!caseInfo && group && !group.investigation?.review_allowed && <p className="small notice notice-warn">Alcance parcial. Amplie os níveis para habilitar a revisão.</p>}
            <button className="btn btn-ghost btn-block" disabled={busy || writing || !cnpj} onClick={() => carrega(cnpj, depth)}>Atualizar consulta</button>
            {caseInfo && <CaseCard info={caseInfo} detail={caseDetail} exposure={caseDetail?.case?.group_exposure ?? caseInfo.group_exposure ?? exposure} />}

            <div className="btn-row">
              <button className="btn btn-ghost" disabled={busy || writing || !caseInfo} onClick={doClose}>
                Encerrar revisão
              </button>
              <button className="btn btn-ghost" disabled={busy || writing} onClick={doReset}>
                Reiniciar demo
              </button>
            </div>
          </section>
        </aside>

        <section className="stage">
          <div className="metrics" role="group" aria-label="Métricas do grupo">
            <Metric k="Empresas" v={fmt(stats?.companies)} />
            <Metric k="Sócios" v={fmt(stats?.partners)} />
            <Metric k="Holdings" v={fmt((group?.nodes ?? []).filter((n) => n.is_holding).length)} />
            <Metric
              k="Limite do grupo"
              v={exposure ? `${exposure.currency} ${compact(exposure.limite)}` : '—'}
            />
            <Metric
              k="Vencido"
              v={exposure ? `${exposure.currency} ${compact(exposure.vencido)}` : '—'}
              tone={exposure?.vencido > 0 ? 'bad' : undefined}
            />
            <Metric k="Tempo" v={stats ? `${stats.elapsed_ms} ms` : '—'} tone={metricTone(stats?.elapsed_ms)} />
            <Metric k="Níveis" v={group?.depth ?? '—'} />
            <span className="spacer" />
            <span className="legend">
              <span><i className="dot" style={{ background: '#00ed64' }} /> solicitante</span>
              <span><i className="dot" style={{ background: 'rgba(4,152,236,.6)' }} /> holding</span>
              <span><i className="dot" style={{ background: '#ff6960' }} /> vencido</span>
              <span><i className="dot" style={{ background: 'rgba(255,192,16,.6)' }} /> sócio PF</span>
              <span><i className="dot dot-flagged" /> em revisão</span>
            </span>
          </div>

          <div className="canvas-wrap">
            {group?.nodes?.length ? (
              <GraphCanvas
                data={group}
                pinned={picked}
                onSelect={setPicked}
                onHover={setHovered}
                foraDoEscopo={tab === 'visibility' ? foraDoEscopo : null}
                destaque={destaque}
              />
            ) : (
              <div className="canvas empty">
                {busy ? 'resolvendo a cadeia…' : error ? error : 'Esta empresa não tem cadeia societária.'}
              </div>
            )}
            {stats?.truncated && (
              <p className="canvas-note">
                truncado em {fmt(stats.max_nodes)} nós — ver <code>LIMITATIONS.md</code>
              </p>
            )}
          </div>
        </section>

        <aside className="inspector" aria-label="Detalhe e evidência">
          <div className="tabs" role="tablist">
            {TABS.map(([key, label]) => (
              <button key={key} role="tab" aria-controls="painel-evidencias" id={`tab-${key}`} className="tab" aria-selected={tab === key} tabIndex={tab === key ? 0 : -1}
                onKeyDown={e => {
                  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
                  e.preventDefault()
                  const i = TABS.findIndex(t => t[0] === key)
                  const next = e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : -1) + TABS.length) % TABS.length
                  setTab(TABS[next][0]); document.getElementById(`tab-${TABS[next][0]}`)?.focus()
                }} onClick={() => setTab(key)}>
                {label}
                {key === 'alerts' && alerts.length > 0 && <span className="tab-count">{alerts.length}</span>}
              </button>
            ))}
          </div>

          <div className="tab-body" role="tabpanel" id="painel-evidencias" aria-labelledby={`tab-${tab}`}>
            {tab === 'evidence' && <EvidencePanel group={group} concentration={conc} caseInfo={caseInfo} alerts={alerts} onPoint={aponta} />}
            {tab === 'company' && (
              <NodePanel
                node={detail}
                neighbours={neighbours}
                byId={byId}
                pinned={pinned}
                currency={exposure?.currency}
                onPick={setPicked}
                onUnpin={() => setPicked(null)}
              />
            )}

            {tab === 'search' && (
              <>
                <StatusLine
                  ready={searchReady}
                  label="Atlas Search"
                  status={Object.values(health?.checks?.search_index ?? {})[0]}
                />
                <p className="muted small">
                  Razão social se escreve de muitos jeitos, e igualdade exata trata as variações
                  como empresas diferentes — é assim que um grupo passa despercebido. Sócios pessoa
                  física também entram na busca: descobrir um grupo costuma começar pelo nome de uma
                  pessoa, não por um CNPJ. Cada resultado diz se já está no grupo que está na tela.
                </p>
                <form onSubmit={doSearch} className="inline-form">
                  <input
                    type="text"
                    value={search.q}
                    onChange={(e) => { ++searchRequest.current; setSearch((p) => ({ ...p, q: e.target.value, out: null, loading: false })) }}
                    aria-label="Razão social"
                    placeholder="ex.: parte do nome do solicitante"
                  />
                  <button className="btn btn-ghost" disabled={busy || search.loading || !searchReady || !search.q.trim()}>Buscar</button>
                </form>
                {/* Escopo é o padrão. Abrir para a base inteira é o gesto de
                    entity resolution — procurar uma empresa que **ainda não** está
                    no grafo e talvez pertença ao grupo. Deixar isso ligado por
                    padrão enchia a lista de homônimos sem relação com a tela. */}
                <label className="check-line">
                  <input
                    type="checkbox"
                    checked={search.todaBase}
                    onChange={(e) => { ++searchRequest.current; setSearch((p) => ({ ...p, todaBase: e.target.checked, out: null, loading: false })) }}
                  />
                  procurar em toda a base (fora do grupo em tela)
                </label>
                {search.loading && <p role="status" className="muted small">Buscando…</p>}
                {search.degraded && <Degraded d={search.degraded} feature="Atlas Search" />}
                {search.out && (
                  <div className="rows">
                    {search.out.warnings?.length > 0 && <p className="notice notice-warn">A busca de sócios está indisponível. Os resultados de empresas continuam disponíveis.</p>}
                    <p className="muted small">{search.out.score_note}</p>
                    {search.out.results.length === 0 && (
                      <p className="muted small">
                        Nada com esse nome no grupo em tela.{' '}
                        {!search.todaBase && 'Marque "procurar em toda a base" para olhar fora dele.'}
                      </p>
                    )}
                    {search.out.results.map((r) => (
                      <button
                        type="button"
                        className={`row as-button ${r.in_group ? 'hit' : ''} ${
                          destaque?.has(r._id) ? 'apontado' : ''
                        }`}
                        key={r._id}
                        // Fora do grupo não existe no grafo: não há o que apontar,
                        // e piscar o grafo inteiro seria pior do que não reagir.
                        onClick={() => r.in_group && aponta([r._id])}
                      >
                        <span className="grow">
                          {r.label}
                          <em>
                            <span className={r.in_group ? 'tag tag-in' : 'tag'}>
                              {r.in_group ? 'no grupo' : 'fora'}
                            </span>
                            <span className="tag">{r.kind === 'person' ? 'sócio' : 'empresa'}</span>
                            {r.kind === 'company' ? `${r.cnpj} · ${r.uf} · ${r.situacao}` : r.occupation}
                          </em>
                          {r.kind === 'company' ? (
                            <em>
                              {r.rating ? `rating ${r.rating} · ` : 'sem crédito no banco · '}
                              limite {money(r.limite, exposure?.currency)}
                              {r.vencido > 0 ? ` · vencido ${money(r.vencido, exposure?.currency)}` : ''}
                            </em>
                          ) : (
                            // Quantas empresas o sócio controla é o número que diz
                            // se vale olhar: sete em grupos diferentes é uma
                            // pergunta, uma é cadastro.
                            <em>
                              participa de {fmt(r.companies)}{' '}
                              {r.companies === 1 ? 'empresa' : 'empresas'} · {r.age_band} ·{' '}
                              {r.income_band}
                            </em>
                          )}
                        </span>
                        <span className="score">{r.score?.toFixed(2)}</span>
                      </button>
                    ))}
                    <p className="muted small">
                      {search.out.companies_found} empresas · {search.out.people_found} sócios ·{' '}
                      {search.out.elapsed_ms} ms
                    </p>
                  </div>
                )}
              </>
            )}

            {tab === 'semantic' && (
              <>
                <StatusLine
                  ready={vectorReady}
                  label="Vector Search"
                  status={Object.values(idx)[2]}
                />
                <p className="muted small">
                  Vários CNAEs diferentes parecem diversificação, e diversificação é o que dilui
                  risco de crédito. Mas &quot;construção de edifícios&quot;, &quot;obras de
                  alvenaria&quot; e &quot;serviços de engenharia de obras&quot; são três códigos e um
                  negócio só. Comparar código não pega isso. Comparar palavra também não — as frases
                  não dividem termo nenhum. Comparar significado pega.
                </p>
                {conc.saturado && <Saturado d={conc.saturado} />}
                {conc.degraded && <Degraded d={conc.degraded} feature="Vector Search" />}
                {conc.loading && <p className="muted small">lendo o grupo…</p>}
                {conc.out?.empty && <p className="muted small">{conc.out.reason}</p>}
                {conc.out && !conc.out.empty && (
                  <div className="rows">
                    <p
                      className={
                        conc.out.dominant_block_share > 0.5 ? 'notice notice-bad' : 'notice notice-ok'
                      }
                    >
                      <b>{Math.round(conc.out.dominant_block_share * 100)}% do limite</b> no bloco semelhante à atividade principal, entre {conc.out.cnae_count} descrições de atividade.

                    </p>
                    {conc.out.activities.map((a) => {
                      const equivalente = conc.out.equivalent_to_dominant.find(
                        (e) => e.activity === a.activity
                      )
                      // As empresas daquela atividade, no grafo em tela.
                      const dela = (group?.nodes ?? [])
                        .filter((n) => n.kind === 'company' && n.activity === a.activity)
                        .map((n) => n.id)
                      const apontado = dela.length > 0 && dela.every((id) => destaque?.has(id))
                      return (
                        <button
                          type="button"
                          className={`row as-button ${equivalente ? 'hit' : ''} ${
                            apontado ? 'apontado' : ''
                          }`}
                          key={a.activity}
                          onClick={() => aponta(dela)}
                        >
                          <span className="grow">
                            {a.activity}
                            <em>
                              {equivalente && (
                                <span className="tag tag-in">semelhante à principal</span>
                              )}
                              {a.companies} {a.companies === 1 ? 'empresa' : 'empresas'} ·{' '}
                              {Math.round(a.share * 100)}% do limite
                            </em>
                            <em className={a.vencido > 0 ? 'bad' : ''}>
                              limite {money(a.limite, conc.out.currency)}
                              {a.vencido > 0 ? ` · vencido ${money(a.vencido, conc.out.currency)}` : ''}
                            </em>
                          </span>
                          {equivalente?.score != null && (
                            <span className="score">{equivalente.score.toFixed(2)}</span>
                          )}
                        </button>
                      )
                    })}
                    <p className="muted small">
                      Similaridade ≥ {conc.out.threshold} inclui a atividade neste bloco.
                      Calibrado neste dado, não é um limiar universal — o score de cada par aparece
                      para o número não virar caixa-preta. Não estima todos os negócios nem o maior bloco global. {conc.out.elapsed_ms} ms · {conc.out.model}
                    </p>
                  </div>
                )}
              </>
            )}

            {tab === 'visibility' && (
              <>
                <p className="muted small">
                  Quem enxerga quais contas é um traversal, não uma lista guardada. A árvore em{' '}
                  <code>advisors.reports_to</code> é a fonte da verdade, e o escopo é derivado dela na
                  hora da consulta — um gerente alcança todos os assessores abaixo dele, um assessor
                  alcança só a própria carteira. Uma lista de visibilidade pré-calculada desatualiza a
                  cada troca de carteira; aqui o alcance é recalculado. Esta é uma simulação de perfis, sem autenticação ou autorização global: o grafo completo continua visível.
                </p>
                <div className="rows">
                  {vis.roster.map((u) => (
                    <button
                      key={u._id}
                      className={`row as-button ${vis.user === u._id ? 'hit' : ''}`}
                      aria-pressed={vis.user === u._id}
                      onClick={() => {
                        setVis((v) => ({ ...v, user: u._id }))
                        // Selecionar já esmaece o que está fora do escopo; apontar
                        // mostra **quais empresas deste grupo** são desta pessoa.
                        // Um assessor com 8 de 25 é uma frase; ver as 8 acesas é
                        // o argumento.
                        aponta(
                          (group?.nodes ?? [])
                            .filter((n) => n.kind === 'company' && n.advisor_id === u._id)
                            .map((n) => n.id)
                        )
                      }}
                    >
                      <span className="grow">
                        {u.nome}
                        <em>
                          {u.papel} · {u.matricula} · {u.regiao}
                        </em>
                      </span>
                    </button>
                  ))}
                </div>
                {vis.loading && <p role="status">Calculando o alcance…</p>}
                {vis.error && !vis.saturado && <p className="notice notice-warn">{vis.error}</p>}
                {vis.saturado && <Saturado d={vis.saturado} />}
                {vis.out && cobertura && (
                  <p className={`notice ${cobertura.dentro === cobertura.total ? 'notice-ok' : 'notice-warn'}`}>
                    Neste grupo econômico, <b>{cobertura.dentro} de {cobertura.total}</b> empresas
                    estão no escopo deste usuário
                    {cobertura.assessores > 1 && (
                      <> — o grupo é atendido por {cobertura.assessores} assessores</>
                    )}
                    . As fora do escopo aparecem esmaecidas no grafo.
                  </p>
                )}
                {vis.check && (
                  <p className={`notice ${vis.check.allowed ? 'notice-ok' : 'notice-bad'}`}>
                    {vis.check.allowed ? 'Visível' : 'Não visível'}: {vis.check.razao_social} —{' '}
                    {vis.check.reason}. A conta é do assessor {vis.check.owner?.nome} (
                    {vis.check.owner?.papel}).
                  </p>
                )}
                {vis.out && (
                  <div className="rows">
                    <p className="notice notice-ok">
                      <b>{vis.out.scope.advisors}</b>{' '}
                      {vis.out.scope.advisors === 1 ? 'pessoa' : 'pessoas'} no escopo ·{' '}
                      {fmt(vis.out.portfolio.companies_with_credit)} contas com crédito · limite{' '}
                      {money(vis.out.portfolio.limite, vis.out.portfolio.currency)}
                      {vis.out.portfolio.vencido > 0 && (
                        <> · vencido {money(vis.out.portfolio.vencido, vis.out.portfolio.currency)}</>
                      )}
                    </p>
                    {vis.out.portfolio.top.slice(0, 8).map((c) => {
                      const noGrafo = (group?.nodes ?? []).some((n) => n.id === c.id)
                      return (
                      <button
                        type="button"
                        className={`row as-button ${destaque?.has(c.id) ? 'apontado' : ''}`}
                        key={c.id}
                        onClick={() => noGrafo && aponta([c.id])}
                        title={noGrafo ? 'Apontar no grafo' : 'Esta conta não está no grupo em tela'}
                      >
                        <span className="grow">
                          {c.razao_social}
                          <em>
                            {c.cnpj} · {c.uf} · {c.rating ?? 'sem rating'}
                          </em>
                        </span>
                        <span className="score">{money(c.utilizado, vis.out.portfolio.currency)}</span>
                      </button>
                      )
                    })}
                    <p className="muted small">
                      Uma agregação: $graphLookup descendo a árvore e a carteira somada no servidor.{' '}
                      {vis.out.elapsed_ms} ms
                    </p>
                  </div>
                )}
              </>
            )}

            {tab === 'alerts' && (
              <>
                <p className="small muted" role="status">Canal de eventos: {streamState}. Após reconexão, atualize a consulta para conferir o estado do caso.</p>
                <StatusLine
                  ready={health?.checks?.change_stream?.running}
                  label="Change Stream"
                  status={health?.checks?.change_stream?.running ? 'READY' : 'stopped'}
                />
                {alerts.length === 0 ? (
                  <p className="muted small">
                    Nada ainda. Abra uma revisão de crédito sobre o grupo: a transação marca as
                    empresas, o change stream acorda e o evento chega aqui sem polling — e encerrar
                    a revisão devolve a contrapartida pelo mesmo caminho.
                  </p>
                ) : (
                  alerts.map((a) =>
                    a.type === 'review_closed' ? (
                      <div className="alert alert-check" key={`${a.case_id}-closed-${a.at}`}>
                        <strong>Revisão encerrada</strong> — {a.companies} empresas voltaram a ativo
                        <em>
                          caso {a.case_id} · evento em {a.stream_ms} ms ·{' '}
                          {new Date(a.at).toLocaleTimeString('pt-BR')}
                        </em>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="alert as-button"
                        key={`${a.case_id}-open-${a.at}`}
                        onClick={() => aponta((group?.nodes ?? []).filter(n => n.case_id === a.case_id).map(n => n.id))}
                      >
                        <strong>{a.companies} empresas</strong> entraram sob revisão de crédito
                        <em>
                          exposição sob revisão: limite{' '}
                          {money(a.under_review_limite, exposure?.currency)}
                          {a.under_review_vencido > 0
                            ? ` · vencido ${money(a.under_review_vencido, exposure?.currency)}`
                            : ''}
                        </em>
                        <em>
                          caso {a.case_id} · evento em {a.stream_ms} ms, soma em {a.lookup_ms} ms ·{' '}
                          {new Date(a.at).toLocaleTimeString('pt-BR')}
                        </em>
                      </button>
                    )
                  )
                )}
              </>
            )}
          </div>

          <QueryDetails
            operation={technical?.operation}
            namespace={technical?.namespace}
            query={technical?.pipeline}
            note={technical?.note}
            metrics={tab !== 'evidence' && tab !== 'company' ? {elapsed_ms: technicalResult?.elapsed_ms, note: 'Tempo da aplicação; não é explain.'} : {
              round_trips: group?.stats?.round_trips,
              elapsed_ms: group?.stats?.elapsed_ms,
              truncated: group?.stats?.truncated,
              note: 'Tempo da aplicação, incluindo rede. Não é explain nem comprovação do índice escolhido.',
            }}
          />
        </aside>
      </main>
    </div>
  )
}

/**
 * A decisão de crédito, na coluna onde a ação foi disparada.
 *
 * Manchete sempre visível; o resto atrás de blocos recolhidos. Com tudo aberto o
 * trilho passa da altura do viewport e o apresentador precisa rolar para achar
 * "Close review" — que é justamente o que não pode acontecer numa demo.
 */
function CaseCard({ info, detail, exposure }) {
  const empresas = detail?.companies ?? []
  return (
    <div className="case-card">
      <p className="case-line">
        <span className="badge badge-bad">revisão aberta</span> <code>{info.case_id}</code>
      </p>
      <dl className="case-grid">
        <div><dt>Empresas bloqueadas</dt><dd>{fmt(detail?.stats?.total ?? info.companies_blocked)}</dd></div>
        <div><dt>Exposições marcadas</dt><dd>{fmt(info.exposures_flagged)}</dd></div>
        <div><dt>Commit</dt><dd>{info.elapsed_ms} ms</dd></div>
        <div><dt>Garantia</dt><dd>{info.read_concern} / {info.write_concern}</dd></div>
      </dl>

      {empresas.length > 0 && (
        <details className="case-compliance">
          <summary>O que a transação mudou</summary>
          <div className="case-diff">
            {empresas.slice(0, 4).map((c) => (
              <p key={c._id} className="case-diff-row">
                <span className="grow">
                  {c.razao_social}
                  <em>{c.cnpj}{c.is_holding ? ' · holding' : ''}</em>
                </span>
                <s>ativa</s> <b>bloqueada</b>
              </p>
            ))}
            {empresas.length > 4 && (
              <p className="muted small">e mais {fmt(empresas.length - 4)} empresas</p>
            )}
            <p className="muted small">
              As três escritas — <code>companies.credit_status</code>,{' '}
              <code>credit_exposure.review_flag</code> e o registro da decisão — commitam juntas.
              Metade do grupo bloqueada com a outra metade liberada é pior do que não decidir: a mesa
              aprova pela porta que ficou aberta, e a auditoria não consegue reconstruir o que foi
              decidido.
            </p>
          </div>
        </details>
      )}

      {exposure && (
        <details className="case-compliance">
          <summary>Quanto vale esta decisão</summary>
          <div className="rows">
            <div className="row compliance">
              <span className="grow">
                <strong>Medido — a exposição do grupo</strong>
                <em>limite {money(exposure.limite, exposure.currency)} em {fmt(exposure.companies_with_credit)} empresas</em>
                <em>utilizado {money(exposure.utilizado, exposure.currency)}</em>
                <em className={exposure.vencido > 0 ? 'bad' : ''}>
                  vencido {money(exposure.vencido, exposure.currency)}
                </em>
                <em>de `credit_exposure`, não é projeção</em>
              </span>
            </div>
            <p className="muted small">
              A exposição é medida. Quanto vale pegar isso antes de aprovar é número do próprio
              banco — não estimamos perda evitada de propósito, pelo mesmo motivo que nenhum
              benchmark aqui é estimado.
            </p>
          </div>
        </details>
      )}
    </div>
  )
}

function NodePanel({ node, neighbours, byId, pinned, currency, onPick, onUnpin }) {
  if (!node) {
    return (
      <p className="muted small">
        Passe o mouse num nó para isolar os vínculos dele, ou clique para fixar o detalhe aqui.
        Caixa é empresa, círculo é sócio pessoa física; a seta aponta de quem controla para quem é
        controlado.
      </p>
    )
  }
  const vizinhos = [...(neighbours.get(node.id) ?? [])]
  const empresa = node.kind === 'company'
  return (
    <div className="node-detail">
      <div className="node-head">
        <h3>{node.label}</h3>
        {pinned && (
          <button className="pin-chip" onClick={onUnpin} title="Soltar o nó">fixado ✕</button>
        )}
      </div>
      {empresa ? (
        <>
          {node.is_subject && <span className="badge badge-ok">solicitante</span>}
          {node.is_holding && <span className="badge badge-warn">holding</span>}
          {node.vencido > 0 && <span className="badge badge-bad">vencido</span>}
          {node.credit_status === 'under_review' && (
            <span className="badge badge-warn">em revisão · {node.case_id}</span>
          )}
          <dl>
            <div><dt>CNPJ</dt><dd>{node.cnpj}</dd></div>
            <div><dt>UF</dt><dd>{node.uf}</dd></div>
            <div><dt>Situação</dt><dd>{node.situacao}</dd></div>
            <div><dt>Porte</dt><dd>{node.porte}</dd></div>
            <div><dt>Atividade</dt><dd className="wrap">{node.activity}</dd></div>
            {/* "R$ 0" e "não tem crédito conosco" são coisas diferentes, e a
                distinção é a que o analista precisa: uma empresa sem relação de
                crédito não entra na exposição do grupo; uma com limite zerado
                entra. Mostrar zero nos dois casos confunde as duas. */}
            {/* Quem responde pela conta. É a pergunta que um gerente faz antes
                de qualquer número, e é o campo que liga os dois cenários da POV:
                a cadeia societária e a hierarquia comercial. */}
            {node.advisor && (
              <div>
                <dt>Assessor</dt>
                <dd>
                  {node.advisor.nome} <em className="muted">· {node.advisor.matricula}</em>
                </dd>
              </div>
            )}
            {node.rating ? (
              <>
                <div><dt>Rating</dt><dd>{node.rating}</dd></div>
                <div><dt>Limite</dt><dd>{money(node.limite, currency)}</dd></div>
                <div><dt>Utilizado</dt><dd>{money(node.utilizado, currency)}</dd></div>
                <div>
                  <dt>Vencido</dt>
                  <dd className={node.vencido > 0 ? 'bad' : ''}>{money(node.vencido, currency)}</dd>
                </div>
              </>
            ) : (
              <div><dt>Crédito</dt><dd>sem relação com este banco</dd></div>
            )}
          </dl>
        </>
      ) : (
        <>
          <span className="badge badge-warn">sócio pessoa física</span>
          <dl>
            <div><dt>Ocupação</dt><dd>{node.occupation ?? '—'}</dd></div>
            <div><dt>Faixa etária</dt><dd>{node.age_band ?? '—'}</dd></div>
            <div><dt>Faixa de renda</dt><dd>{node.income_band ?? '—'}</dd></div>
          </dl>
          <p className="muted small">
            Uma pessoa com participação em empresas de grupos diferentes é como um grupo oculto
            aparece — a cadeia societária chega nela, o cadastro da empresa não.
          </p>
        </>
      )}

      {vizinhos.length > 0 && (
        <>
          <h4>Vínculos diretos</h4>
          <div className="chips">
            {vizinhos.map((id) => {
              const v = byId.get(id)
              return (
                <button key={id} className={`chip ${v?.vencido > 0 ? 'chip-ring' : ''}`} onClick={() => onPick(id)}>
                  {v?.label ?? id.slice(0, 12)}
                </button>
              )
            })}
          </div>
        </>
      )}
      <code className="id">{node.id}</code>
      {pinned && (
        <p className="muted small">
          Nó fixado: passar o mouse no grafo continua isolando vínculos, mas não troca mais este
          painel.
        </p>
      )}
    </div>
  )
}

function Metric({ k, v, tone }) {
  return (
    <div className={`metric ${tone ?? ''}`}>
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  )
}

function metricTone(ms) {
  if (ms == null) return undefined
  if (ms < 500) return 'good'
  if (ms < 2000) return 'warn'
  return 'bad'
}

function HealthBadge({ health }) {
  if (!health) return <span className="badge badge-warn">verificando…</span>
  if (health.status === 'ok') return <span className="badge badge-ok">✓ tudo pronto</span>
  if (health.status === 'offline') return <span className="badge badge-bad">✕ backend fora do ar</span>
  return <span className="badge badge-warn">⚠ degradado</span>
}

function StatusLine({ ready, label, status }) {
  return (
    <p className="status-line">
      <span className={`badge ${ready ? 'badge-ok' : 'badge-warn'}`}>{label} {status ?? '—'}</span>
    </p>
  )
}

/**
 * Saturação, que não é indisponibilidade.
 *
 * O backend limita quantas consultas analíticas rodam ao mesmo tempo e recusa o
 * excedente com 429 em vez de enfileirar. Medido: sem esse limite, 64 clientes
 * simultâneos levavam a p95 da consulta da tela de 308 ms para 2 s. A tela diz o
 * que aconteceu, porque "tente de novo" e "está fora do ar" pedem reações
 * diferentes de quem está apresentando.
 */
function Saturado({ d }) {
  return (
    <p className="notice notice-warn">
      Consulta analítica saturada — {d.hint ?? 'tente de novo em instantes.'} Recusar cedo é
      deliberado: preserva a latência da consulta interativa.
    </p>
  )
}

function Degraded({ d, feature }) {
  return (
    <p className="notice notice-warn">
      {feature} indisponível (<code>{d.index ?? '—'}</code>: {d.status}). O traversal continua
      funcionando — a degradação é por recurso, não por tela.
    </p>
  )
}
