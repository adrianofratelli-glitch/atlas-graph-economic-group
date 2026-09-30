import test from 'node:test'
import assert from 'node:assert/strict'
import { evidencePath } from '../src/evidence.js'
const group = {subject:{id:'a'},edges:[{from:'r',to:'a'},{from:'r',to:'b'},{from:'b',to:'c'},{from:'c',to:'r'}]}
test('evidence includes applicant and path to overdue sibling',()=>assert.deepEqual(new Set(evidencePath(group,['b'])),new Set(['a','r','b'])))
test('cycles terminate and disconnected targets never highlight unrelated nodes',()=>assert.deepEqual(evidencePath(group,['missing']),[]))
test('empty group has no evidence',()=>assert.deepEqual(evidencePath(null,['b']),[]))
