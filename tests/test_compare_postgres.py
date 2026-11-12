"""Contrato da comparação MongoDB × PostgreSQL, sem servidor nenhum.

A comparação só vale se os dois lados reduzirem a resposta da mesma forma. Estes
testes travam as regras que a rodada local já mostrou serem fáceis de errar: a
aresta para um controlador fora do cadastro, os motivos de alcance e a recusa
de um banco PostgreSQL que não seja descartável.
"""
import inspect
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "queries"))
import compare_postgres as cmp  # noqa: E402
from app.db import ownership  # noqa: E402


def _fake_conn(doc):
    cur = MagicMock()
    cur.fetchone.return_value = (doc,)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn


# A mesma resposta lógica nos dois formatos: um controlador ausente do cadastro.
PG_DOC = {
    "subject": "b", "missing": 1,
    "edges": [{"owner_id": "fantasma", "owned_id": "a", "owner_type": "corporate", "percentage": 100},
              {"owner_id": "a", "owned_id": "b", "owner_type": "corporate", "percentage": 100}],
    "companies": [{"id": "a", "limite": "100.10", "utilizado": 50, "vencido": 0},
                  {"id": "b", "limite": None, "utilizado": None, "vencido": None}],
    "people": [],
    **{k: False for k in cmp.FLAGS},
}
MONGO_DOC = {
    "_id": "b", "ids_empresa": ["a", "b", "fantasma"], "ids_pessoa": [],
    "arestas": [{"owner_id": "fantasma", "owned_id": "a", "owner_type": "corporate", "percentage": 100},
                {"owner_id": "a", "owned_id": "b", "owner_type": "corporate", "percentage": 100}],
    "empresas": [{"_id": "a"}, {"_id": "b"}], "pessoas": [],
    "creditos": [{"company_id": "a", "limite": 100.10, "utilizado": 50, "vencido": 0}],
    **{k: False for k in cmp.FLAGS},
}


class CompareContractTests(unittest.TestCase):
    def test_flags_are_the_reasons_the_pov_reports(self):
        # Se o núcleo ganhar um motivo novo, a comparação precisa aprender a calculá-lo.
        source = inspect.getsource(ownership.economic_group)
        for flag in cmp.FLAGS:
            self.assertIn(f'"{flag}"', source)
        self.assertEqual(source.count('"edges_limited_') + source.count('"depth_limited_')
                         + source.count('"roots_limited"') + source.count('"root_unresolved"'), len(cmp.FLAGS))

    def test_both_sides_reduce_a_missing_controller_the_same_way(self):
        pg = cmp.pg_group(_fake_conn(PG_DOC), "b", 4, 3600, 5)
        with patch("app.db.client.bounded_aggregate", return_value=[MONGO_DOC]):
            mongo = cmp.mongo_raw(MagicMock(), ownership, "b", 4, 3600)
        self.assertEqual(pg, mongo)
        self.assertEqual(pg["edges"], [("a", "b", "corporate", 100.0)])
        self.assertEqual(pg["reasons"], ["missing_entities"])
        self.assertEqual(pg["exposure_cents"]["limite"], 10010)

    def test_product_response_maps_to_the_same_contract(self):
        result = {"found": True, "coverage": {"reasons": ["missing_entities", "node_limit"]},
                  "nodes": [{"id": "a", "kind": "company", "limite": 100.10, "utilizado": 50, "vencido": 0},
                            {"id": "b", "kind": "company", "limite": 0.0, "utilizado": 0.0, "vencido": 0.0}],
                  "edges": [{"from": "a", "to": "b", "type": "corporate", "percentage": 100}]}
        self.assertEqual(cmp.mongo_contract(result), cmp.pg_group(_fake_conn(PG_DOC), "b", 4, 3600, 5))

    def test_unknown_subject_is_not_found_on_both_sides(self):
        self.assertEqual(cmp.pg_group(_fake_conn(None), "x", 4, 3600, 5), {"found": False})
        with patch("app.db.client.bounded_aggregate", return_value=[]):
            self.assertEqual(cmp.mongo_raw(MagicMock(), ownership, "x", 4, 3600), {"found": False})

    def test_refuses_a_postgres_database_that_is_not_disposable(self):
        argv = ["compare_postgres.py", "--mongo-uri", "mongodb://127.0.0.1:1", "--pg-uri", "postgresql://u@h/producao"]
        with patch.object(sys, "argv", argv), patch("sys.stderr"), self.assertRaises(SystemExit) as exc:
            cmp.main()
        self.assertEqual(exc.exception.code, 2)

    def test_fixtures_query_an_existing_company(self):
        cells = [cmp.tree(d, b, f"t{d}{b}") for b in (1, 2, 3) for d in (1, 2, 4, 6)]
        cells += [fx for fx, _ in cmp.hostile().values()]
        for fx in cells:
            ids = {c["_id"] for c in fx["companies"]}
            self.assertIn(fx["subject"], ids)
            self.assertEqual(len({e["_id"] for e in fx["edges"]}), len(fx["edges"]))
        self.assertEqual(len(cmp.tree(6, 3, "x")["companies"]), 1093)


if __name__ == "__main__":
    unittest.main()
