import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { validEvidenceReport } from '../src/evidenceReport.js'
const report = JSON.parse(readFileSync(new URL('../public/evidence/results.json',import.meta.url)))
test('published evidence has all fields required by the page',()=>assert.equal(validEvidenceReport(report),true))
test('malformed and non-finite reports are rejected before rendering',()=>{
  for (const d of [null,{}, {...report,audit:[null]}, {...report,curve:[{...report.curve[0],p95_ms:Infinity}]}, {...report,audit:[{...report.audit[0],members:[null]}]}]) assert.equal(validEvidenceReport(d),false)
})
test('a valid failed audit remains visible as a failure, not a load error',()=>assert.equal(validEvidenceReport({...report,audit:[{...report.audit[0],checks:{membership:false}}]}),true))

import { validComparisonReport } from '../src/evidenceReport.js'
const comparison = JSON.parse(readFileSync(new URL('../public/evidence/comparison.json',import.meta.url)))
test('published comparison has every field the page renders',()=>assert.equal(validComparisonReport(comparison),true))
test('comparison without timings for an equal cell, or with a pending status, is rejected',()=>{
  const cell = comparison.cells.find(c => c.same_result)
  for (const d of [null, {...comparison,status:'deferred'}, {...comparison,cells:[{...cell,latency:{}}]},
                   {...comparison,cells:[{...cell,latency:{1:{mongodb:{...cell.latency['1'].mongodb,p95_ms:NaN},postgresql:cell.latency['1'].postgresql}}}]}])
    assert.equal(validComparisonReport(d),false)
})
test('a divergent cell stays renderable without timings: correctness failure is shown, not hidden',()=>{
  const cell = {...comparison.cells[0],same_result:false,latency:undefined}
  assert.equal(validComparisonReport({...comparison,passed:false,cells:[cell]}),true)
})
test('comparison needs runs per cell and a text-only list of what was not measured',()=>{
  for (const d of [{...comparison,method:{...comparison.method,runs_per_cell:undefined}},
                   {...comparison,method:{...comparison.method,runs_per_cell:0}},
                   {...comparison,not_measured:[{text:'x'}]}, {...comparison,not_measured:'x'}])
    assert.equal(validComparisonReport(d),false)
})

import { loadNote } from '../src/evidenceReport.js'
test('busy run is labelled preliminary under load, with start and end',()=>{
  const n = loadNote({host_load_average:{start:[44.8,31.9,20.2],end:[39.9,31.7,20.5]}})
  assert.equal(n.busy,true)
  assert.match(n.text,/Medição preliminar sob carga/)
  assert.match(n.text,/44,8 → 39,9/)
})
test('missing end omits the final value instead of printing NaN',()=>{
  const n = loadNote({host_load_average:{start:[44.8]}})
  assert.doesNotMatch(n.text,/NaN|→/)
  assert.match(n.text,/44,8 no início/)
})
test('idle run is not called preliminary, and no load data renders nothing',()=>{
  assert.equal(loadNote({host_load_average:{start:[0.8],end:[1.1]}}).busy,false)
  assert.equal(loadNote({}),null)
  assert.equal(loadNote({host_load_average:{start:[]}}),null)
})
