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
