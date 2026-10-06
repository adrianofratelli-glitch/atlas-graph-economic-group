"""Grafos hostis no Atlas real, em banco efêmero exclusivo.

Uso: backend/venv/bin/python tests/live_graph_adversarial.py

Complementa `live_hardening.py` (transação, Change Streams, ciclo simples) com
topologias que tentam explodir o `$graphLookup`: hub com milhares de filhas,
ciclo longo, auto-laço, cadeia além do cap, aresta para nó inexistente, prazo
de agregação estourado e concorrência sobre o hub. Cria
`graph_adversarial_test_<uuid>`, nunca toca o banco da demo e remove o banco
criado mesmo quando falha. Resultado em `tests/live-graph-adversarial-results.json`.
"""
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

name = "graph_adversarial_test_" + uuid.uuid4().hex
os.environ["MONGODB_DB"] = name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.client import get_client, get_db  # noqa: E402
from app.services.alerts import hub  # noqa: E402
from main import app  # noqa: E402

HUB_CHILDREN = 5000
CHAIN = 20
checks: list[dict] = []
timings: dict[str, float] = {}


def check(label: str, condition: bool, detail: str = "") -> None:
    checks.append({"test": label, "passed": bool(condition), "detail": detail})
    print(("PASS " if condition else "FAIL ") + label + (f"  — {detail}" if detail else ""), flush=True)


def company(key: str, **extra) -> dict:
    return {"_id": "company_" + key, "cnpj": key, "razao_social": extra.pop("nome", key),
            "advisor_id": "advisor_a", "credit_status": "active", **extra}


def edge(i: str, owner: str, owned: str, kind: str = "corporate") -> dict:
    return {"_id": i, "owner_id": owner, "owned_id": owned, "owner_type": kind, "percentage": 10}


def seed(db) -> None:
    assert db.name == name and name.startswith("graph_adversarial_test_")
    companies = [company("hub", is_holding=True)]
    companies += [company(f"leaf{i}") for i in range(HUB_CHILDREN)]
    companies += [company(f"cyc{i}") for i in range(3)]
    companies += [company("self")]
    companies += [company(f"ch{i}") for i in range(CHAIN)]
    companies += [company("dangling")]
    companies += [company("unicode", nome="Ação ⚡ ‮שלום​ Ltda")]
    db.companies.insert_many(companies, ordered=False)
    edges = [edge(f"h{i}", "company_hub", f"company_leaf{i}") for i in range(HUB_CHILDREN)]
    edges += [edge("c0", "company_cyc0", "company_cyc1"), edge("c1", "company_cyc1", "company_cyc2"),
              edge("c2", "company_cyc2", "company_cyc0")]
    edges += [edge("s0", "company_self", "company_self")]
    edges += [edge(f"ch{i}", f"company_ch{i}", f"company_ch{i + 1}") for i in range(CHAIN - 1)]
    edges += [edge("d0", "company_ghost", "company_dangling")]
    edges += [edge("u0", "person_ghost", "company_unicode", "individual")]
    db.ownership.insert_many(edges, ordered=False)
    db.advisors.insert_one({"_id": "advisor_a", "nome": "A", "reports_to": None, "papel": "assessor"})
    db.companies.create_index("cnpj", unique=True)
    db.ownership.create_index("owned_id")
    db.ownership.create_index("owner_id")
    db.credit_exposure.create_index("company_id", unique=True)


def timed(api, path):
    t0 = time.perf_counter()
    r = api.get(path)
    return r, round((time.perf_counter() - t0) * 1000, 1)


def run() -> None:
    db = get_db()
    seed(db)
    s = get_settings()
    with TestClient(app, raise_server_exceptions=False) as api:
        r, ms = timed(api, "/api/group/leaf7?depth=6")
        timings["hub_leaf_depth6_ms"] = ms
        body = r.json() if r.status_code == 200 else {}
        check("hub de 5.000 filhas responde 200", r.status_code == 200, f"HTTP {r.status_code} em {ms} ms")
        check("hub respeita o teto de nós", len(body.get("nodes", [])) <= s.max_nodes, str(len(body.get("nodes", []))))
        check("hub declara cobertura parcial", body.get("coverage", {}).get("complete") is False,
              str(body.get("coverage")))
        check("hub preserva o sujeito", any(n.get("is_subject") for n in body.get("nodes", [])))
        check("hub parcial não autoriza revisão", body.get("investigation", {}).get("review_allowed") is False)
        check("hub termina antes do prazo de agregação", ms < s.graph_max_time_ms, f"{ms} ms")

        r = api.get("/api/group/cyc0?depth=6")
        b = r.json()
        pares = [(e["from"], e["to"]) for e in b.get("edges", [])]
        check("ciclo de 3 termina", r.status_code == 200, f"HTTP {r.status_code}")
        check("ciclo não duplica arestas", len(pares) == len(set(pares)) == 3, str(len(pares)))
        check("ciclo sem raiz resolvida é parcial", b["coverage"]["complete"] is False, str(b["coverage"]))

        r = api.get("/api/group/self?depth=6")
        check("auto-laço termina sem 500", r.status_code == 200, f"HTTP {r.status_code}")
        check("auto-laço não multiplica nós", r.status_code == 200 and r.json()["stats"]["companies"] == 1)

        r = api.get(f"/api/group/ch{CHAIN - 1}?depth=999")
        b = r.json()
        check("cadeia de 20 é limitada ao cap", r.status_code == 200 and b["depth"] == s.depth_cap, str(b.get("depth")))
        check("cadeia além do cap declara fronteira", "depth_limited_up" in b["coverage"]["reasons"],
              str(b["coverage"]["reasons"]))

        r = api.get("/api/group/dangling?depth=3")
        b = r.json()
        check("aresta para dono inexistente não quebra", r.status_code == 200, f"HTTP {r.status_code}")
        check("entidade ausente declarada", "missing_entities" in b["coverage"]["reasons"], str(b["coverage"]))

        r = api.get("/api/group/unicode?depth=2")
        check("razão social com RTL/emoji/zero-width volta intacta",
              r.status_code == 200 and any("⚡" in n["label"] for n in r.json()["nodes"]))

        for path in ["/api/group/" + '{"$gt":""}', "/api/group/%00", "/api/group/" + "​"]:
            r = api.get(path)
            check(f"id malformado {path[11:21]!r} vira 404", r.status_code == 404, f"HTTP {r.status_code}")

        before = s.graph_max_time_ms
        s.graph_max_time_ms = 1
        try:
            r = api.get("/api/group/leaf7?depth=6")
        finally:
            s.graph_max_time_ms = before
        detail = r.json().get("detail", {}) if r.status_code != 200 else {}
        check("maxTimeMS estourado vira 503 com dica", r.status_code == 503 and "hint" in detail,
              f"HTTP {r.status_code} {str(detail)[:80]}")

        # Tese: o traversal lê o dado operacional, sem cópia nem ETL. Uma aresta
        # gravada agora aparece na próxima consulta, sem janela de sincronização.
        lags = []
        for i in range(10):
            db.companies.insert_one(company(f"fresh{i}"))
            t0 = time.perf_counter()
            db.ownership.insert_one(edge(f"f{i}", "company_cyc0", f"company_fresh{i}"))
            r = api.get(f"/api/group/fresh{i}?depth=2")
            lags.append(round((time.perf_counter() - t0) * 1000, 1))
            if r.status_code != 200 or not any(e["from"] == "company_cyc0" for e in r.json()["edges"]):
                lags.append(None)
                break
        timings["write_to_traversal_ms"] = lags
        check("aresta recém-gravada aparece no traversal seguinte (sem ETL)", None not in lags and len(lags) == 10,
              f"gravação→leitura p50 {sorted(lags)[len(lags)//2] if None not in lags else '?'} ms")

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=20) as pool:
            res = list(pool.map(lambda _: timed(api, "/api/group/leaf3?depth=6"), range(40)))
        timings["hub_concurrent_40x20_wall_s"] = round(time.perf_counter() - t0, 1)
        lat = sorted(ms for _, ms in res)
        timings["hub_concurrent_p50_ms"] = lat[len(lat) // 2]
        timings["hub_concurrent_p95_ms"] = lat[int(len(lat) * 0.95)]
        codes = sorted({r.status_code for r, _ in res})
        check("40 traversals concorrentes no hub sem 500", all(c in (200, 429, 503) for c in codes), str(codes))
        sets = {len(r.json()["nodes"]) for r, _ in res if r.status_code == 200}
        check("concorrência devolve resultado determinístico", len(sets) == 1, str(sets))


if __name__ == "__main__":
    try:
        run()
    finally:
        hub.stop()
        if hub._thread:
            hub._thread.join(timeout=5)
        client = get_client()
        assert get_db().name == name and name.startswith("graph_adversarial_test_")
        client.drop_database(name)
        removed = name not in client.list_database_names()
        Path(__file__).with_name("live-graph-adversarial-results.json").write_text(json.dumps(
            {"checks": checks, "timings": timings, "isolated_database_removed": removed}, indent=2,
            ensure_ascii=False))
        client.close()
        failed = [c["test"] for c in checks if not c["passed"]]
        print(f"\n{len(checks) - len(failed)}/{len(checks)} passaram; banco removido: {removed}")
        sys.exit(1 if failed else 0)
