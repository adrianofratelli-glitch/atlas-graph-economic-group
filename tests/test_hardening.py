"""Regressões offline: fraude no comprovante, limites, falhas e eventos fora de ordem."""
import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from pymongo.errors import ConnectionFailure, ExecutionTimeout
from app.services import investigation
from app.services.alerts import AlertHub
from app.db import credit_decision, ownership, concentration, search, hierarchy
from app.services import limits
from main import app


def group():
    return {"found": True, "subject": {"id": "c1", "cnpj": "123"}, "depth": 2,
            "nodes": [{"id": "c1", "kind": "company", "limite": 10, "utilizado": 2, "vencido": 0}],
            "edges": [], "group_exposure": {"limite": 10, "vencido": 0},
            "coverage": {"complete": True, "reasons": []}}


class ProofTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(investigation.verify(investigation.issue(group())["token"])["cnpj"], "123")

    def test_tamper_and_malformed(self):
        token = investigation.issue(group())["token"]
        for value in ["", "...", token[:-2] + "xx", token + ".x", "☃.bad"]:
            with self.subTest(value=value[:10]), self.assertRaises(investigation.InvestigationError):
                investigation.verify(value)

    def test_expired(self):
        token = investigation.issue(group())["token"]
        with patch.object(investigation.time, "time", return_value=10**12), self.assertRaises(investigation.InvestigationError):
            investigation.verify(token)

    def test_fingerprint_changes_on_exposure_or_membership(self):
        a = group(); b = copy.deepcopy(a); b["nodes"][0]["limite"] += 1
        self.assertNotEqual(investigation.fingerprint(a), investigation.fingerprint(b))
        b = copy.deepcopy(a); b["nodes"].append({"id": "c2"})
        self.assertNotEqual(investigation.fingerprint(a), investigation.fingerprint(b))

    def test_fingerprint_ignores_order_and_review_flag(self):
        a = group(); b = copy.deepcopy(a); b["nodes"][0]["credit_status"] = "under_review"
        self.assertEqual(investigation.fingerprint(a), investigation.fingerprint(b))


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.client = MagicMock()
        self.session = self.client.start_session.return_value.__enter__.return_value
        self.session.with_transaction.side_effect = lambda fn, **kw: fn(self.session)
        self.db.credit_decisions.find_one.return_value = None
        self.db.companies.find_one.return_value = None
        self.db.companies.update_many.return_value.matched_count = 1
        self.db.companies.update_many.return_value.modified_count = 1
        self.db.credit_exposure.update_many.return_value.modified_count = 1
        for target, value in [("get_db", self.db), ("get_client", self.client)]:
            p = patch.object(credit_decision, target, return_value=value); p.start(); self.addCleanup(p.stop)
        self.g = group()
        p = patch.object(ownership, "economic_group", return_value=self.g)
        self.lookup = p.start(); self.addCleanup(p.stop)
        self.token = investigation.issue(self.g)["token"]

    def test_server_uses_snapshot_and_recomputed_exposure(self):
        result = credit_decision.open_review(self.token, "teste")
        self.assertTrue(result["ok"])
        self.assertEqual(self.lookup.call_args.kwargs["session"], self.session)
        stored = self.db.credit_decisions.insert_one.call_args.args[0]
        self.assertEqual(stored["group_exposure"], self.g["group_exposure"])
        self.assertEqual(stored["company_ids"], ["c1"])

    def test_changed_data_blocks_all_writes(self):
        self.g["group_exposure"]["limite"] = 900
        with self.assertRaises(investigation.InvestigationError): credit_decision.open_review(self.token, "teste")
        self.db.companies.update_many.assert_not_called()

    def test_partial_blocks_all_writes(self):
        self.g["coverage"]["complete"] = False
        with self.assertRaises(investigation.InvestigationError): credit_decision.open_review(self.token, "teste")
        self.db.companies.update_many.assert_not_called()

    def test_overlap_does_not_write(self):
        self.db.companies.find_one.return_value = {"case_id": "other"}
        out = credit_decision.open_review(self.token, "teste")
        self.assertEqual(out["case_id"], "other"); self.assertFalse(out["ok"])
        self.db.companies.update_many.assert_not_called()

    def test_replay_is_idempotent(self):
        self.db.credit_decisions.find_one.return_value = {"status": "open", "company_ids": ["c1"],
            "companies_blocked": 1, "exposures_flagged": 1, "group_exposure": {"limite": 10}}
        out = credit_decision.open_review(self.token, "teste")
        self.assertTrue(out["replayed"]); self.lookup.assert_not_called()
        self.db.companies.update_many.assert_not_called()

    def test_closed_proof_cannot_reopen(self):
        self.db.credit_decisions.find_one.return_value = {"status": "closed"}
        with self.assertRaises(investigation.InvestigationError): credit_decision.open_review(self.token, "teste")

    def test_failed_transaction_propagates(self):
        self.session.with_transaction.side_effect = ConnectionFailure("injected")
        with self.assertRaises(ConnectionFailure): credit_decision.open_review(self.token, "teste")


class EndpointTests(unittest.TestCase):
    def setUp(self): self.api = TestClient(app, raise_server_exceptions=False)
    def tearDown(self): self.api.close()

    def test_old_client_cannot_supply_totals_or_companies(self):
        r = self.api.post('/api/credit/review', json={"company_ids": ["c1"], "group_exposure": {"limite": -1}})
        self.assertEqual(r.status_code, 422)

    def test_extra_fields_rejected(self):
        r = self.api.post('/api/credit/review', json={"investigation_token": investigation.issue(group())["token"], "analyst": "admin"})
        self.assertEqual(r.status_code, 422)

    def test_blank_reason(self):
        r = self.api.post('/api/credit/review', json={"investigation_token": "x" * 30, "reason": " "})
        self.assertEqual(r.status_code, 422)

    def test_tampered_proof_is_conflict(self):
        r = self.api.post('/api/credit/review', json={"investigation_token": "x" * 30})
        self.assertEqual(r.status_code, 409)

    def test_alert_limit_is_bounded(self):
        for n in [0, -1, 101]:
            self.assertEqual(self.api.get('/api/alerts/recent', params={"limit": n}).status_code, 422)

    def test_database_failures_become_503(self):
        for path, target, body in [('/api/analysis/concentration', 'group_concentration', {"company_ids": ["x"]})]:
            with patch.object(concentration, target, side_effect=ConnectionFailure('secret uri')):
                r = self.api.post(path, json=body)
                self.assertEqual(r.status_code, 503); self.assertNotIn('secret uri', r.text)


class AlertTests(unittest.TestCase):
    def event(self, close=False):
        fields = {"credit_status": "active" if close else "under_review",
                  "last_review_case_id" if close else "case_id": "case"}
        return {"documentKey": {"_id": "c1"}, "updateDescription": {"updatedFields": fields,
                "removedFields": ["case_id"] if close else []}, "fullDocument": {}}

    def test_fast_open_close_are_distinct(self):
        hub = AlertHub(); pending = {}
        hub._acumula(self.event(), pending); hub._acumula(self.event(True), pending)
        self.assertEqual({a['kind'] for a in pending.values()}, {'review_opened', 'review_closed'})

    def test_replayed_event_not_double_counted(self):
        hub = AlertHub(); pending = {}
        for _ in range(3): hub._acumula(self.event(), pending)
        self.assertEqual(next(iter(pending.values()))['companies'], 1)

    def test_close_after_restart_uses_event_case(self):
        hub = AlertHub(); pending = {}; hub._acumula(self.event(True), pending)
        self.assertEqual(next(iter(pending.values()))['case_id'], 'case')

    def test_failed_persist_preserves_pending(self):
        hub = AlertHub(); pending = {}; hub._acumula(self.event(), pending)
        with patch('app.services.alerts.time.monotonic', return_value=10**12), patch.object(hub, '_monta', side_effect=ConnectionFailure):
            with self.assertRaises(ConnectionFailure): hub._descarrega(pending)
        self.assertEqual(len(pending), 1)

    def test_slow_subscriber_does_not_block_others(self):
        hub = AlertHub(); slow = hub.subscribe(); fast = hub.subscribe()
        for _ in range(slow.maxsize): slow.put_nowait({})
        hub._publish({'case_id': 'x'})
        self.assertEqual(fast.get_nowait()['case_id'], 'x')
        hub.unsubscribe(slow); hub.unsubscribe(fast)
        self.assertEqual(len(hub._subscribers), 0)


class ConcentrationTests(unittest.TestCase):
    def test_threshold_uses_unrounded_score_and_no_global_business_count(self):
        db = MagicMock()
        db.companies.aggregate.return_value = [
            {"_id": "A", "companies": 1, "limite": 40, "vencido": 0},
            {"_id": "B", "companies": 1, "limite": 30, "vencido": 0},
            {"_id": "C", "companies": 1, "limite": 30, "vencido": 0}]
        db.activities.find_one.return_value = {"embedding": [1., 0.]}
        db.activities.aggregate.return_value = [{"_id": "A", "score": 1}, {"_id": "B", "score": .79999}, {"_id": "C", "score": .2}]
        with patch.object(concentration, 'get_db', return_value=db), patch.object(concentration, 'index_status', return_value='READY'):
            out = concentration.group_concentration(['c1'])
        self.assertEqual(out['dominant_block_share'], .4)
        self.assertNotIn('distinct_businesses', out)
        self.assertTrue(out['query_details']['pipeline'][0]['$vectorSearch']['exact'])
        self.assertIsInstance(out['query_details']['pipeline'][0]['$vectorSearch']['queryVector'], str)

    def test_unavailable_index_is_explicit(self):
        db = MagicMock(); db.companies.aggregate.return_value = [{"_id": "A", "companies": 1, "limite": 0, "vencido": 0}]
        with patch.object(concentration, 'get_db', return_value=db), patch.object(concentration, 'index_status', return_value='BUILDING'):
            with self.assertRaises(concentration.IndexUnavailable): concentration.group_concentration(['c1'])

    def test_empty_scope_does_not_query(self):
        db = MagicMock()
        with patch.object(concentration, 'get_db', return_value=db): self.assertTrue(concentration.group_concentration([])['empty'])
        db.companies.aggregate.assert_not_called()


class SearchTests(unittest.TestCase):
    def test_empty_scope_never_becomes_global(self):
        db = MagicMock()
        with patch.object(search, 'get_db', return_value=db), patch.object(search, 'index_status', return_value='READY'):
            out = search.resolve_company('A', company_ids=[], node_ids=[])
        self.assertEqual(out['results'], [])
        self.assertTrue(out['scoped'])
        db.companies.aggregate.assert_not_called()
        db.people.aggregate.assert_not_called()

    def test_people_count_is_in_pipeline_not_n_plus_one(self):
        db = MagicMock()
        db.people.aggregate.return_value = [{'_id': 'person_a', 'name': 'A', 'score': 1, 'companies': 7}]
        traces = []
        with patch.object(search, 'get_db', return_value=db), patch.object(search, 'index_status', return_value='READY'):
            out = search._busca_socios('A', 10, {'person_a'}, True, traces)
        self.assertEqual(out[0]['companies'], 7)
        self.assertEqual(db.people.aggregate.call_count, 1)
        db.ownership.count_documents.assert_not_called()
        self.assertTrue(any('$lookup' in stage for stage in traces[0]['pipeline']))

    def test_people_failure_is_visible_without_losing_companies(self):
        db = MagicMock()
        db.companies.aggregate.return_value = [{'_id': 'c', 'razao_social': 'A', 'score': 1}]
        db.people.aggregate.side_effect = RuntimeError('injected')
        with patch.object(search, 'get_db', return_value=db), patch.object(search, 'index_status', return_value='READY'):
            out = search.resolve_company('A', company_ids=['c'], node_ids=['person_a'])
        self.assertEqual(out['companies_found'], 1)
        self.assertEqual(out['warnings'], [{'feature': 'people_search', 'status': 'QUERY_FAILED'}])

    def test_explicit_global_search_still_queries(self):
        db = MagicMock(); db.companies.aggregate.return_value = []
        with patch.object(search, 'get_db', return_value=db), patch.object(search, 'index_status', return_value='READY'):
            out = search.resolve_company('A', company_ids=[], escopo_apenas=False)
        db.companies.aggregate.assert_called_once()
        self.assertFalse(out['scoped'])


class FailureBoundaryTests(unittest.TestCase):
    def test_bulkhead_releases_slot_after_exception(self):
        with self.assertRaises(ValueError):
            with limits.vaga('concentracao'): raise ValueError('injected')
        semaphore = limits._semaforos['concentracao']
        for _ in range(limits.VAGAS['concentracao']): self.assertTrue(semaphore.acquire(blocking=False))
        try:
            with patch.object(limits, 'ESPERA_S', 0), self.assertRaises(Exception) as ctx:
                with limits.vaga('concentracao'): pass
            self.assertEqual(ctx.exception.status_code, 429)
            self.assertEqual(ctx.exception.headers['Retry-After'], '2')
        finally:
            for _ in range(limits.VAGAS['concentracao']): semaphore.release()

    def test_portfolio_timeout_is_not_user_not_found(self):
        db = MagicMock(); db.advisors.aggregate.side_effect = ExecutionTimeout('injected')
        with patch.object(hierarchy, 'get_db', return_value=db), self.assertRaises(ExecutionTimeout):
            hierarchy.portfolio('advisor')

    def test_closed_event_ignores_newer_full_document_case(self):
        hub = AlertHub(); pending = {}
        event = AlertTests().event(True)
        event['fullDocument'] = {'case_id': 'new-case', 'credit_status': 'under_review'}
        hub._acumula(event, pending)
        self.assertEqual(next(iter(pending.values()))['case_id'], 'case')

    def test_alert_uses_transaction_snapshot_not_current_exposure(self):
        db = MagicMock()
        db.credit_decisions.find_one.return_value = {'group_exposure': {'limite': 42, 'vencido': 3}, 'companies_blocked': 4}
        hub = AlertHub()
        with patch('app.services.alerts.get_db', return_value=db):
            event = hub._monta({'kind': 'review_opened', 'case_id': 'c', 'company_ids': ['a'], 'companies': 1, 'primeiro_em': 0})
        self.assertEqual(event['under_review_limite'], 42)
        self.assertEqual(event['companies'], 4)
        self.assertEqual(event['events_coalesced'], 1)
        db.credit_exposure.aggregate.assert_not_called()

    def test_deep_health_does_not_claim_failed_probe_is_healthy(self):
        import main
        db = MagicMock(); db.companies.find_one.return_value = {'cnpj': '123'}
        with patch.object(main, 'get_db', return_value=db), patch.object(main.search, 'index_status', return_value='READY'), patch.object(main.ownership, 'economic_group', return_value={'found': False, 'too_large': True}):
            # Counts are normal ints so the health body remains serialisable.
            for name in ['companies', 'ownership', 'credit_exposure', 'people']:
                getattr(db, name).estimated_document_count.return_value = 1
            out = main.health()
        self.assertEqual(out['status'], 'degraded')
        self.assertFalse(out['checks']['graphlookup_probe']['ok'])


if __name__ == '__main__': unittest.main(verbosity=2)
