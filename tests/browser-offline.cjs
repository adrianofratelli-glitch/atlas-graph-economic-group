/* Deterministic browser faults. Every API request is intercepted; no Atlas access. */
const {chromium} = require('playwright')
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const results=[]
async function check(name, fn) { await fn(); results.push({test:name,passed:true}); console.log('PASS',name) }
;(async()=>{
 const browser=await chromium.launch({headless:true})
 try {
  const page=await browser.newPage({viewport:{width:1440,height:1000}})
  const errors=[];page.on('pageerror',e=>errors.push(e.message))
  await page.addInitScript(()=>{
   window.EventSource=class { constructor(){setTimeout(()=>this.onopen?.(),0)} close(){} }
  })
  let opened=false, reviewCalls=0, vectorFail=false, portfolioBusy=false, uncertain=false
  const exp={currency:'R$',limite:300,utilizado:150,vencido:20,companies_with_credit:3}
  const nodes=['a','root','b'].map((id,i)=>({id,kind:'company',label:['Solicitante','Holding','Empresa com atraso'][i],cnpj:id,is_subject:id==='a',is_holding:id==='root',advisor_id:'user',limite:100,utilizado:50,vencido:id==='b'?20:0,rating:'A',activity:'Atividade',level:i===1?0:1}))
  const json=(route,data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)})
  await page.route('**/health',route=>json(route,{status:'ok',checks:{search_index:{company:'READY',people:'READY',vector:'READY'},change_stream:{running:true}}}))
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url()),path=url.pathname
   if(path==='/api/entry-points')return json(route,{applicants:[{cnpj:'a',razao_social:'Solicitante',group_levels:6}],control:[]})
   if(path==='/api/hierarchy/roster')return json(route,{users:[{_id:'user',nome:'Perfil de teste',papel:'assessor'}]})
   if(path.startsWith('/api/group/')){
    const depth=Number(url.searchParams.get('depth'))
    return json(route,{found:true,depth,subject:{...nodes[0],razao_social:'Solicitante'},nodes:nodes.map(n=>({...n,credit_status:opened?'under_review':'active',case_id:opened?'case':null})),edges:[{from:'root',to:'a',type:'corporate',percentage:100},{from:'root',to:'b',type:'corporate',percentage:100}],stats:{companies:3,partners:0,edges:2,elapsed_ms:12,round_trips:1},group_exposure:exp,coverage:{complete:depth>=2},investigation:{token:'test-proof',review_allowed:depth>=2},query_details:{operation:'aggregate',namespace:'fixture.companies',pipeline:[{$match:{cnpj:'a'}}]}})
   }
   if(path==='/api/analysis/concentration')return vectorFail?json(route,{detail:{feature:'vector_search',index:'vector',status:'MISSING'}},503):json(route,{ok:true,empty:false,cnae_count:1,equivalent_activity_count:1,dominant_block_share:1,activities:[{activity:'Atividade',companies:3,limite:300,vencido:20,share:1}],equivalent_to_dominant:[{activity:'Atividade',score:1}],threshold:.8,elapsed_ms:15})
   if(path==='/api/credit/review'){
    reviewCalls++
    assert.deepEqual(Object.keys(route.request().postDataJSON()).sort(),['investigation_token','reason'])
    await new Promise(r=>setTimeout(r,150));opened=true
    if(uncertain)return route.abort('connectionreset')
    return json(route,{ok:true,case_id:'case',companies_blocked:3,exposures_flagged:3,elapsed_ms:20,read_concern:'snapshot',write_concern:'majority',group_exposure:exp})
   }
   if(path==='/api/credit/case/case')return json(route,{ok:true,case:{exposures_flagged:3,group_exposure:exp},companies:nodes.map(n=>({...n,_id:n.id,razao_social:n.label})),stats:{total:3,truncated:false}})
   if(path==='/api/credit/close/case'){opened=false;return json(route,{ok:true,case_id:'case'})}
   if(path==='/api/demo/reset'){opened=false;return json(route,{ok:true})}
   if(path==='/api/search/companies'){
    const body=route.request().postDataJSON()
    if(body.q==='old') await new Promise(r=>setTimeout(r,800))
    return json(route,{companies_found:1,people_found:0,results:[{_id:'a',kind:'company',label:body.q,razao_social:body.q,in_group:true,score:1}],warnings:[]})
   }
   if(path.includes('/portfolio'))return portfolioBusy?json(route,{detail:{feature:'carteira',error:'saturado',hint:'Repita em alguns segundos.'}},429):json(route,{user:{id:'user'},team:[],scope:{advisors:1},portfolio:{companies_with_credit:3,limite:300,vencido:20,top:[]}})
   if(path.includes('/can-see/'))return json(route,{allowed:true,razao_social:'Solicitante',reason:'no escopo',owner:{nome:'Perfil'}})
   throw new Error('Unmocked API: '+path)
  })
  await page.goto('http://127.0.0.1:5350/')
  await page.getByRole('button',{name:'Abrir revisão sobre 3 empresas'}).waitFor()
  await check('double click gera uma única escrita', async()=>{
   await page.getByRole('button',{name:'Abrir revisão sobre 3 empresas'}).evaluate(b=>{b.click();b.click()})
   await page.getByText('revisão aberta',{exact:true}).waitFor()
   assert.equal(reviewCalls,1)
  })
  await check('encerrar revisão libera a interface',async()=>{
   await page.getByRole('button',{name:'Encerrar revisão'}).click()
   await page.getByRole('button',{name:'Abrir revisão sobre 3 empresas'}).waitFor()
   assert.equal(opened,false)
  })
  await check('escrita sem resposta pode ser reconciliada',async()=>{
   uncertain=true
   await page.getByRole('button',{name:'Abrir revisão sobre 3 empresas'}).click()
   await page.getByRole('alert').waitFor()
   assert.match(await page.getByRole('alert').textContent(),/conferir o resultado/)
   await page.getByRole('button',{name:'Atualizar consulta',exact:true}).click()
   await page.getByText('revisão aberta',{exact:true}).waitFor()
   assert.equal(reviewCalls,2)
   uncertain=false
   await page.getByRole('button',{name:'Encerrar revisão'}).click()
   await page.getByRole('button',{name:'Abrir revisão sobre 3 empresas'}).waitFor()
  })
  await check('resultado antigo não reaparece ao editar busca',async()=>{
   await page.getByRole('tab',{name:'Busca',exact:true}).click()
   const input=page.getByRole('textbox',{name:'Razão social'})
   await input.fill('old');await page.getByRole('button',{name:'Buscar',exact:true}).click()
   await input.fill('new')
   await page.waitForTimeout(1000)
   assert.equal(await page.getByRole('button',{name:/old/}).count(),0)
  })
  await check('texto hostil é renderizado como texto',async()=>{
   await page.getByRole('textbox',{name:'Razão social'}).fill('<img src=x onerror=alert(1)>')
   await page.getByRole('button',{name:'Buscar',exact:true}).click()
   await page.getByText('<img src=x onerror=alert(1)>',{exact:false}).waitFor()
   assert.equal(await page.locator('.tab-body img').count(),0)
  })
  await check('índice vetorial ausente degrada só o painel',async()=>{
   vectorFail=true
   await page.getByRole('button',{name:'Atualizar consulta',exact:true}).click()
   await page.getByRole('tab',{name:'Concentração',exact:true}).click()
   await page.getByText('MISSING',{exact:false}).waitFor()
   assert(await page.getByRole('button',{name:'Abrir revisão sobre 3 empresas'}).isEnabled())
  })
  await check('429 de carteira tem mensagem e não derruba grafo',async()=>{
   portfolioBusy=true
   await page.getByRole('tab',{name:'Visibilidade',exact:true}).click()
   await page.getByRole('button',{name:/Perfil de teste/}).click()
   await page.getByText(/Repita em alguns segundos/).waitFor()
   assert.equal(await page.locator('canvas').count(),1)
  })
  await check('navegação de abas por teclado',async()=>{
   const tab=page.getByRole('tab',{name:'Visibilidade',exact:true})
   await tab.focus();await page.keyboard.press('Home')
   assert.equal(await page.getByRole('tab',{name:'Resumo',exact:true}).getAttribute('aria-selected'),'true')
   await page.keyboard.press('End')
   assert.equal(await page.getByRole('tab',{name:'Alertas',exact:true}).getAttribute('aria-selected'),'true')
  })
  await check('link de pular conteúdo move o foco',async()=>{
   await page.getByRole('link',{name:'Pular para o conteúdo'}).focus()
   await page.keyboard.press('Enter')
   assert.equal(await page.evaluate(()=>document.activeElement.id),'conteudo-principal')
  })
  await check('abas não sobrepõem seus textos em 360px',async()=>{
   await page.setViewportSize({width:360,height:800})
   assert(await page.getByRole('tab',{name:'Concentração',exact:true}).evaluate(e=>e.scrollWidth<=e.clientWidth+1))
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1))
  })
  await check('sem exceções JavaScript nas falhas simuladas',async()=>assert.deepEqual(errors,[]))
 } finally {await browser.close();await fs.writeFile('tests/browser-offline-results.json',JSON.stringify(results,null,2))}
})().catch(e=>{console.error(e);process.exitCode=1})
