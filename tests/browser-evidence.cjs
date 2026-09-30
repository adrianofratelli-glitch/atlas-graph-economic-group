const { chromium } = require('playwright')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const results=[]
async function check(test,fn) { await fn();results.push({test,passed:true});console.log('PASS',test) }
;(async()=>{
 const browser=await chromium.launch({headless:true})
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}})
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.goto('http://127.0.0.1:5350/evidence')
  await page.getByRole('heading',{name:'Da empresa à soma do grupo'}).waitFor()
  await check('auditoria carrega e todos os grupos são navegáveis',async()=>{
   const select=page.getByLabel('Grupo de referência')
   assert.equal(await select.locator('option').count(),6)
   for(let i=0;i<6;i++){await select.selectOption(String(i));await page.getByText('Conferido',{exact:true}).waitFor()}
   assert.equal(await page.getByText('43 empresas',{exact:true}).count(),2)
  })
  await check('plano real e membros são inspecionáveis',async()=>{
   await page.getByText('Conferir as 43 empresas e seus valores',{exact:true}).click()
   assert(await page.getByRole('cell',{name:/R\$/}).count()>50)
   await page.getByText('Verificação e plano de execução',{exact:true}).click()
   assert((await page.locator('pre').innerText()).includes('EXPRESS_IXSCAN'))
   await page.getByText('Verificação e plano de execução',{exact:true}).click()
   await page.getByText('Conferir as 43 empresas e seus valores',{exact:true}).click()
  })
  await check('limite parcial e comparação pendente estão explícitos',async()=>{
   await page.getByText('Parcial · revisão bloqueada',{exact:true}).waitFor()
   await page.getByRole('heading',{name:'PostgreSQL: medição pendente'}).waitFor()
  })
  for(const width of [1440,768,360]){
   await page.setViewportSize({width,height:1000})
   await check(`página de evidências sem overflow ${width}`,async()=>assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)))
  }
  await page.setViewportSize({width:1440,height:1000})
  await page.locator('#audit').screenshot({path:'docs/screenshots/08-investigacao-auditavel.png'})
  await page.setViewportSize({width:1440,height:1900})
  await page.locator('#curve').scrollIntoViewIfNeeded()
  await page.locator('#curve').screenshot({path:'docs/screenshots/09-curva-de-comportamento.png'})
  await check('JSON publicado está acessível',async()=>{
   const r=await page.request.get('http://127.0.0.1:5350/evidence/results.json');assert.equal(r.status(),200)
   assert.equal((await r.json()).passed,true)
  })
  await check('falha do relatório tem recuperação',async()=>{
   await page.route('**/evidence/results.json',r=>r.fulfill({status:503,body:'unavailable'}))
   await page.reload();await page.getByRole('alert').waitFor()
   await page.unroute('**/evidence/results.json');await page.getByRole('button',{name:'Tentar novamente'}).click()
   await page.getByRole('heading',{name:'Da empresa à soma do grupo'}).waitFor()
  })
  await check('JSON inválido não derruba a página',async()=>{
   await page.route('**/evidence/results.json',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({schema_version:1,audit:[null],curve:[null]})}))
   await page.reload();await page.getByRole('alert').waitFor()
   await page.unroute('**/evidence/results.json')
  })
  await check('sem exceções JavaScript',async()=>assert.deepEqual(errors,[]))
 } finally {await browser.close();await fs.writeFile('tests/browser-evidence-results.json',JSON.stringify({measured_at:new Date().toISOString(),checks:results},null,2)+'\n')}
})().catch(e=>{console.error(e);process.exitCode=1})
