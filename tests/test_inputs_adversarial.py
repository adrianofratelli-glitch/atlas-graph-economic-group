"""Entradas hostis sem banco: tamanho de comando, campos extras e SSE sem thread.

Roda offline (sem Atlas). Cada caso nasceu de uma falha reproduzida na revisão
adversarial de 2026-10.
"""
import asyncio
import os
import queue
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect, DocumentTooLarge, NetworkTimeout

from app.db import client as dbclient
from app.services import alerts
from app.services.alerts import AlertHub, TooManySubscribers, sse_stream_async
from main import app


class OversizedPayloadTests(unittest.TestCase):
    def setUp(self):
        self.api = TestClient(app, raise_server_exceptions=False)

    def test_concentration_rejects_huge_ids_before_driver(self):
        # Antes: 2.000 ids de 10 KB viravam DocumentTooLarge e HTTP 500.
        r = self.api.post("/api/analysis/concentration", json={"company_ids": ["x" * 10_000] * 2000})
        self.assertEqual(r.status_code, 422)

    def test_search_rejects_huge_node_ids(self):
        r = self.api.post("/api/search/companies", json={"q": "Ltda", "node_ids": ["n" * 1000] * 10})
        self.assertEqual(r.status_code, 422)

    def test_unknown_fields_are_refused(self):
        for path, body in [
            ("/api/search/companies", {"q": "Ltda", "$where": "1"}),
            ("/api/analysis/concentration", {"company_ids": ["company_a"], "$where": "1"}),
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.api.post(path, json=body).status_code, 422)

    def test_operator_objects_in_id_lists_are_refused(self):
        r = self.api.post("/api/analysis/concentration", json={"company_ids": [{"$gt": ""}]})
        self.assertEqual(r.status_code, 422)

    def test_driver_document_too_large_becomes_413_not_500(self):
        with patch("app.db.concentration.group_concentration", side_effect=DocumentTooLarge("too large")):
            r = self.api.post("/api/analysis/concentration", json={"company_ids": ["company_a"]})
        self.assertEqual(r.status_code, 413)
        self.assertIn("hint", r.json()["detail"])


class TimeoutTests(unittest.TestCase):
    def test_bounded_aggregate_scopes_csot_deadline(self):
        # Com timeoutMS no cliente, maxTimeMS por operação é ignorado; o teto
        # precisa vir de pymongo.timeout().
        seen = {}

        class Coll:
            def aggregate(self, pipeline, **kw):
                from pymongo import _csot
                seen["remaining"] = _csot.remaining()
                seen["kw"] = kw
                return iter([])

        with patch.object(dbclient.get_settings(), "graph_max_time_ms", 1500):
            dbclient.bounded_aggregate(Coll(), [])
        self.assertIsNotNone(seen["remaining"])
        self.assertLessEqual(seen["remaining"], 1.5)
        self.assertNotIn("maxTimeMS", seen["kw"])

    def test_timeout_is_not_retried(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise NetworkTimeout("timed out (configured timeouts: timeoutMS: 1ms)")

        with patch.object(NetworkTimeout, "timeout", property(lambda self: True)):
            with self.assertRaises(NetworkTimeout):
                dbclient.with_retry(boom)
        self.assertEqual(calls["n"], 1)

    def test_transient_reconnect_still_retried(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise AutoReconnect("blip")
            return "ok"

        with patch.object(dbclient.time, "sleep"):
            self.assertEqual(dbclient.with_retry(flaky), "ok")
        self.assertEqual(calls["n"], 2)

    def test_client_side_timeout_becomes_503_with_hint(self):
        api = TestClient(app, raise_server_exceptions=False)
        exc = NetworkTimeout("shard-00.example.mongodb.net:27017: timed out")
        with patch.object(NetworkTimeout, "timeout", property(lambda self: True)), \
             patch("app.db.ownership.bounded_aggregate", side_effect=exc):
            r = api.get("/api/group/123?depth=6")
        self.assertEqual(r.status_code, 503)
        self.assertIn("hint", r.json()["detail"])
        self.assertIn("tempo limite", r.json()["detail"]["error"])
        self.assertNotIn("mongodb.net", r.text)

    def test_unavailable_body_never_leaks_cluster_host(self):
        api = TestClient(app, raise_server_exceptions=False)
        exc = AutoReconnect("cluster0-shard-00-02.example.mongodb.net:27017: connection closed")
        with patch("app.db.hierarchy.portfolio", side_effect=exc):
            r = api.get("/api/hierarchy/advisor_x/portfolio")
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("mongodb.net", r.text)


class SseTests(unittest.TestCase):
    def test_subscriber_cap(self):
        hub = AlertHub()
        subs = [hub.subscribe() for _ in range(alerts.MAX_SUBSCRIBERS)]
        with self.assertRaises(TooManySubscribers):
            hub.subscribe()
        for q in subs:
            hub.unsubscribe(q)
        hub.unsubscribe(hub.subscribe())

    def test_async_stream_does_not_block_and_stops_on_disconnect(self):
        q: queue.Queue = queue.Queue()
        q.put_nowait({"type": "review_opened", "nome": "Ação ✓ ‮"})
        calls = {"n": 0}

        async def disconnected():
            calls["n"] += 1
            return calls["n"] > 1

        async def collect():
            out = []
            async for chunk in sse_stream_async(q, disconnected):
                out.append(chunk)
            return out

        with patch.object(alerts, "POLL_S", 0.01):
            chunks = asyncio.run(asyncio.wait_for(collect(), timeout=5))
        self.assertEqual(chunks[0], ": conectado\n\n")
        self.assertTrue(chunks[1].startswith("data: "))

    def test_stream_route_returns_503_when_full(self):
        api = TestClient(app, raise_server_exceptions=False)
        with patch.object(alerts.hub, "subscribe", side_effect=TooManySubscribers("cheio")):
            r = api.get("/api/alerts/stream")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"]["feature"], "alerts_stream")


class SeedGuardTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data-generator"))
        import common
        self.common = common

    def test_demo_db_refused_without_flag(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ALLOW_DEMO_DB_WRITE", None)
            for name in ["graph_grupo_economico", "graph_grupo_economico_test_backup", "prod"]:
                with self.subTest(name=name), self.assertRaises(SystemExit):
                    self.common.assert_write_allowed(name)

    def test_test_db_and_explicit_flag_allowed(self):
        os.environ.pop("ALLOW_DEMO_DB_WRITE", None)
        self.common.assert_write_allowed("graph_grupo_economico_test")
        with patch.dict(os.environ, {"ALLOW_DEMO_DB_WRITE": "1"}):
            self.common.assert_write_allowed("graph_grupo_economico")

    def test_flag_must_be_exactly_one(self):
        with patch.dict(os.environ, {"ALLOW_DEMO_DB_WRITE": "true"}), self.assertRaises(SystemExit):
            self.common.assert_write_allowed("graph_grupo_economico")


if __name__ == "__main__":
    unittest.main()
