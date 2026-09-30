"""Verifica os instrumentos; não deriva a referência do traversal sob teste."""
import importlib.util
import sys
import unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location('evidence_measure', Path(__file__).parents[1] / 'queries/build_evidence.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class EvidenceMeasurementTests(unittest.TestCase):
    def test_cent_precision_and_nearest_rank(self):
        self.assertEqual(m.cents(0.1+0.2), 30)
        self.assertEqual(m.stats(list(range(1,21)))['p95_ms'], 19)
        self.assertEqual(m.stats(list(range(1,11)))['p95_ms'], 10)

    def test_fixture_counts_and_credit_are_independent(self):
        companies, edges, credits = m.tree(4,3,'test')
        self.assertEqual(len(companies), 121)
        self.assertEqual(len(edges), 120)
        self.assertEqual(sum(c['limite'] for c in credits), 12100)
        self.assertEqual(sum(c['vencido'] for c in credits), 10)
        self.assertEqual(len({c['_id'] for c in companies}),121)

    def test_path_terminates_with_cycles_and_disconnected_target(self):
        edges = [{'owner_id':a,'owned_id':b} for a,b in [('a','b'),('b','c'),('c','a')]]
        self.assertEqual(m.path_between(edges,'a','c'), ['a','c'])
        self.assertEqual(m.path_between(edges,'a','z'), [])

    def test_explain_does_not_double_count_or_publish_host(self):
        rows = m.explain_summary({'serverInfo':{'host':'private'}, 'command':{'secret':'private'},
             'stages':[{'$cursor':{'executionStats':{'totalDocsExamined':1,'executionStages':{'totalDocsExamined':1}}}},
                       {'$graphLookup':{},'usedDisk':False}]})
        self.assertEqual(len(rows),3)
        self.assertNotIn('private',str(rows))
        self.assertTrue(any('usedDisk' in r for r in rows))

if __name__ == '__main__': unittest.main()
