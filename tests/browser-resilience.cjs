/* Run with Playwright available on NODE_PATH. Live read-only UI + isolated fault injection. */
const { chromium } = require('playwright')
const fs = require('node:fs/promises')
const assert = require('node:assert/strict')
const results = []
async function check(label, fn) { await fn(); results.push({test: label, passed: true}); console.log('PASS', label) }
;(async () => {
  const browser = await chromium.launch({headless:true})
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}})
    const errors=[]; page.on('pageerror', e => errors.push(e.message))
    await page.goto('http://127.0.0.1:5350/')
    await page.getByRole('heading', {name:'O que esta consulta revela'}).waitFor({timeout:60000})
    await check('resumo e consulta parcial bloqueiam revisão', async () => {
      assert(await page.getByRole('button', {name:/Abrir revisão sobre/}).isDisabled())
      assert(await page.getByText('Consulta parcial.', {exact:false}).count() > 0)
    })
    await page.getByRole('button', {name:'6', exact:true}).click()
    await page.getByRole('button', {name:/Abrir revisão sobre/}).waitFor()
    await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(b => b.textContent.includes('Abrir revisão sobre'))?.disabled, {timeout:60000})
    await check('grupo completo habilita revisão', async () => assert(await page.getByRole('button', {name:'Abrir revisão sobre 43 empresas'}).isEnabled()))
    await check('evidência aponta os vínculos no grafo', async () => {
      await page.getByRole('button', {name:/Mostrar vínculos/}).click()
      assert(await page.locator('canvas').count() > 0)
    })
    for (const [width,height] of [[1440,1000],[768,1024],[360,800]]) {
      await page.setViewportSize({width,height})
      await page.waitForTimeout(250)
      await check(`sem overflow horizontal ${width}`, async () => {
        const size = await page.evaluate(() => ({scroll:document.documentElement.scrollWidth, width:innerWidth}))
        assert(size.scroll <= size.width + 1, JSON.stringify(size))
      })
      await page.screenshot({path:`/tmp/graph-ui-${width}.png`,fullPage:true})
      await check(`abas acessíveis ${width}`, async () => {
        for (const title of ['Empresa','Busca','Concentração','Visibilidade','Alertas','Resumo']) {
          await page.getByRole('tab', {name:title, exact:true}).click()
          assert.equal(await page.getByRole('tab', {name:title, exact:true}).getAttribute('aria-selected'), 'true')
        }
      })
    }
    await page.setViewportSize({width:1440,height:1000})
    await page.getByRole('tab', {name:'Concentração', exact:true}).click()
    await check('métrica sem contagem fictícia de negócios', async () => {
      await page.getByText(/no bloco semelhante à atividade principal/).waitFor({timeout:60000})
      assert.equal(await page.getByText(/negócios distintos/).count(),0)
    })
    await page.getByRole('tab', {name:'Visibilidade', exact:true}).click()
    await check('simulação de perfil identificada', async () => assert(await page.getByText(/sem autenticação ou autorização global/).isVisible()))
    // Delay an obsolete depth response; it must never replace the last choice.
    await page.route('**/api/group/**', async route => {
      if (new URL(route.request().url()).searchParams.get('depth') === '1') {
        const response = await route.fetch(); await new Promise(r=>setTimeout(r,1800));
        await route.fulfill({response})
      } else await route.continue()
    })
    await page.getByRole('button',{name:'1',exact:true}).click()
    await page.waitForTimeout(50)
    await page.getByRole('button',{name:'6',exact:true}).click()
    await page.waitForTimeout(5000)
    await check('resposta atrasada não troca o grupo atual', async () => assert(await page.getByRole('button',{name:'Abrir revisão sobre 43 empresas'}).isEnabled()))
    await page.unroute('**/api/group/**')
    await check('sem exceções JavaScript no fluxo ao vivo', async () => assert.deepEqual(errors,[]))
    await page.close()

    const offline = await browser.newPage()
    await offline.route('**/api/**', route => route.abort('connectionrefused'))
    await offline.route('**/health', route => route.abort('connectionrefused'))
    await offline.goto('http://127.0.0.1:5350/')
    await check('offline exibe erro e recuperação', async () => {
      await offline.getByRole('button',{name:'Tentar novamente'}).waitFor({timeout:10000})
      assert(await offline.getByRole('alert').isVisible())
    })
    await offline.screenshot({path:'/tmp/graph-ui-offline.png',fullPage:true})
    await offline.unroute('**/api/**'); await offline.unroute('**/health')
    await offline.getByRole('button',{name:'Tentar novamente'}).click()
    await check('recuperação após offline', async () => await offline.getByRole('heading',{name:'O que esta consulta revela'}).waitFor({timeout:60000}))
    await offline.close()
  } finally {
    await fs.writeFile('tests/browser-results.json', JSON.stringify(results,null,2))
    await browser.close()
  }
})().catch(e=>{ console.error(e); process.exitCode=1 })
