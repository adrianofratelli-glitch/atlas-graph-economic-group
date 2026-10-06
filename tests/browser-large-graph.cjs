/* UI com o grafo no teto de exibição (1.200 nós). Toda chamada de API é interceptada; sem Atlas.
 * Uso: NODE_PATH=<node_modules com playwright> node tests/browser-large-graph.cjs (com ./start.sh no ar). */
const {chromium} = require('playwright')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const results = []
async function check(name, fn) { await fn(); results.push({test: name, passed: true}); console.log('PASS', name) }
;(async () => {
  const browser = await chromium.launch({headless: true})
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}})
    const errors = []; page.on('pageerror', e => errors.push(e.message))
    await page.addInitScript(() => { window.EventSource = class { constructor() { setTimeout(() => this.onopen?.(), 0) } close() {} } })
    const N = 1200
    const nodes = [{id: 'hub', kind: 'company', label: 'Holding ⚡ ‮שלום', cnpj: 'hub', is_holding: true, is_subject: false, limite: 100, utilizado: 50, vencido: 0, rating: 'A', activity: 'Holding', advisor_id: 'user'}]
    for (let i = 1; i < N; i++) nodes.push({id: 'c' + i, kind: 'company', label: 'Controlada ' + i, cnpj: 'c' + i, is_subject: i === 1, limite: 100, utilizado: 50, vencido: i === 7 ? 10 : 0, rating: 'B', activity: 'Atividade', advisor_id: 'user'})
    const edges = nodes.slice(1).map(n => ({from: 'hub', to: n.id, type: 'corporate', percentage: 10}))
    const json = (route, data, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(data)})
    await page.route('**/health', r => json(r, {status: 'ok', checks: {search_index: {}, change_stream: {running: true}}}))
    await page.route('**/api/**', async route => {
      const path = new URL(route.request().url()).pathname
      if (path === '/api/entry-points') return json(route, {applicants: [{cnpj: 'c1', razao_social: 'Controlada 1', group_levels: 1}], control: []})
      if (path === '/api/hierarchy/roster') return json(route, {users: []})
      if (path.startsWith('/api/group/')) return json(route, {found: true, depth: 1, subject: {id: 'c1', cnpj: 'c1', razao_social: 'Controlada 1', limite: 100, utilizado: 50, vencido: 0, rating: 'B'},
        nodes, edges, stats: {companies: N, partners: 0, edges: edges.length, truncated: true, max_nodes: N, elapsed_ms: 900, round_trips: 1},
        group_exposure: {currency: 'R$', limite: N * 100, utilizado: N * 50, vencido: 10, companies_with_credit: N},
        coverage: {complete: false, reasons: ['edges_limited_down', 'node_limit']}, investigation: {token: 'x'.repeat(30), review_allowed: false},
        query_details: {operation: 'aggregate', namespace: 'fixture.companies', pipeline: [{$match: {cnpj: 'c1'}}]}})
      if (path === '/api/analysis/concentration') return json(route, {ok: true, empty: true})
      if (path === '/api/alerts/recent') return json(route, {alerts: [], listener: {running: true}})
      return json(route, {detail: 'não usado'}, 404)
    })
    const t0 = Date.now()
    await page.goto('http://127.0.0.1:5350/')
    // Pronto = a rede do vis-network tem as 1.200 posições e as 1.199 arestas.
    await page.waitForFunction(n => window.__grafo && Object.keys(window.__grafo.posicoes()).length >= n
      && window.__grafo.arestas().length >= n - 1, N, {timeout: 60000})
    const renderMs = Date.now() - t0
    await page.getByText('Consulta parcial.', {exact: false}).first().waitFor({timeout: 60000})
    await check(`grafo de ${N} nós renderiza em menos de 15 s (${renderMs} ms)`, async () => assert(renderMs < 15000))
    await check('teto de exibição bloqueia revisão', async () => assert(await page.getByRole('button', {name: /Abrir revisão sobre/}).isDisabled()))
    await check('página continua responsiva após o render', async () => {
      const t = Date.now(); await page.evaluate(() => new Promise(r => requestAnimationFrame(() => r()))); assert(Date.now() - t < 2000)
    })
    for (const [w, h] of [[768, 1024], [360, 800]]) {
      await page.setViewportSize({width: w, height: h}); await page.waitForTimeout(300)
      await check(`sem overflow horizontal ${w} com grafo grande`, async () => {
        const s = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, width: innerWidth}))
        assert(s.scroll <= s.width + 1, JSON.stringify(s))
      })
    }
    await check('sem exceções JavaScript', async () => assert.deepEqual(errors, []))
    await fs.writeFile('tests/browser-large-graph-results.json', JSON.stringify({nodes: N, render_ms: renderMs, results}, null, 2) + '\n')
  } finally { await browser.close() }
})().catch(e => { console.error(e); process.exit(1) })
