#!/usr/bin/env python3
"""Recria **todo** o estado da demo com um comando. Idempotente.

    .venv/bin/python scripts/reset_demo.py                       # banco do .env
    MONGODB_DB=graph_grupo_economico_test .venv/bin/python scripts/reset_demo.py --scale small
    ALLOW_DEMO_DB_WRITE=1 .venv/bin/python scripts/reset_demo.py # banco da demo

Etapas, na ordem que importa:

1. sócios pessoa física (`generate_people.py`);
2. base societária, exposição e grupos de vitrine (`generate_ownership.py`,
   que lê `people`);
3. hierarquia comercial (`generate_advisors.py`);
4. índices B-tree do traversal e da carteira (`schema/indexes.js` via mongosh,
   com `MONGODB_DB` repassado — antes ele ia sempre para o banco padrão);
5. estado de revisão da apresentação: casos, alertas, arestas simuladas e
   marcas de revisão (o mesmo que `POST /api/demo/reset`);
6. vetores das atividades (`embed_activities.py`, Voyage; sem chave, pula e o
   painel semântico degrada);
7. índices Atlas Search e Vector Search (`schema/search_indexes.py`), esperando
   `READY`;
8. conferência: contagens, índices do `$graphLookup`, status dos índices de
   busca e um traversal de referência num grupo de vitrine.

Recusa qualquer banco que não termine em `_test` sem `ALLOW_DEMO_DB_WRITE=1`.

Volume: `--scale full` (padrão) reproduz a demo — 800 mil pessoas, 1,2 milhão de
empresas, 40 mil grupos. `--scale small` (20 mil pessoas, 30 mil empresas,
1.000 grupos, os mesmos 40 grupos de vitrine) existe para validar o reset num
banco `_test` em poucos minutos; não serve para medir latência. As variáveis
`PEOPLE`, `COMPANIES`, `ECON_GROUPS` e `SHOWCASE` sobrepõem a escala escolhida.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "data-generator"))
from common import assert_write_allowed, db_name, get_db  # noqa: E402

SCALES = {
    "full": {"PEOPLE": 800_000, "COMPANIES": 1_200_000, "ECON_GROUPS": 40_000, "SHOWCASE": 40},
    "small": {"PEOPLE": 20_000, "COMPANIES": 30_000, "ECON_GROUPS": 1_000, "SHOWCASE": 40},
}
TRAVERSAL_INDEXES = ("owner_id_1", "owned_id_1")
SEARCH_INDEXES = (
    ("companies", "ATLAS_SEARCH_INDEX_NAME", "companies_name_resolution"),
    ("people", "PEOPLE_SEARCH_INDEX_NAME", "people_name_resolution"),
    ("activities", "VECTOR_INDEX_NAME", "activities_vector"),
)


def step(n: int, total: int, label: str) -> float:
    print(f"\n▶ {n}/{total} {label}", flush=True)
    return time.perf_counter()


def done(t0: float) -> None:
    print(f"  ✓ {time.perf_counter() - t0:.1f} s", flush=True)


def run(cmd: list[str], env: dict[str, str]) -> None:
    # Sem `check=True`: o CalledProcessError imprimiria o comando, e o comando
    # do mongosh carrega a connection string.
    rc = subprocess.run(cmd, cwd=BASE, env=env).returncode
    if rc != 0:
        raise SystemExit(f"❌ etapa falhou ({Path(cmd[1]).name if len(cmd) > 1 else cmd[0]}, código {rc})"
                         if cmd[0] != "mongosh" else f"❌ etapa falhou (mongosh, código {rc})")


def reset_review_state(db) -> dict[str, int]:
    """Mesmo efeito de `credit_decision.reset_all()` (POST /api/demo/reset)."""
    c = db.companies.update_many(
        {"credit_status": "under_review"},
        {"$set": {"credit_status": "active"}, "$unset": {"case_id": "", "reviewed_at": ""}},
    )
    e = db.credit_exposure.update_many({"review_flag": True}, {"$unset": {"review_flag": "", "case_id": ""}})
    return {
        "companies_restored": c.modified_count,
        "exposures_restored": e.modified_count,
        "cases_removed": db.credit_decisions.delete_many({}).deleted_count,
        "alerts_removed": db.ownership_alerts.delete_many({}).deleted_count,
        "simulated_edges_removed": db.ownership.delete_many({"simulated": True}).deleted_count,
    }


def verify(db) -> list[str]:
    problems = []
    for coll in ("people", "companies", "ownership", "credit_exposure", "advisors", "economic_groups"):
        n = db[coll].estimated_document_count()
        print(f"  {coll}: {n:,}")
        if n == 0:
            problems.append(f"{coll} vazia")
    names = {i["name"] for i in db.ownership.list_indexes()}
    for idx in TRAVERSAL_INDEXES:
        if idx not in names:
            problems.append(f"falta o índice ownership.{idx}")
    for coll, env, default in SEARCH_INDEXES:
        name = os.getenv(env, default)
        try:
            found = next((i for i in db[coll].list_search_indexes() if i["name"] == name), None)
            st = found["status"] if found else "MISSING"
        except Exception as exc:  # noqa: BLE001 — relata, não derruba a conferência
            st = f"UNSUPPORTED ({type(exc).__name__})"
        print(f"  search {coll}.{name}: {st}")
        if st != "READY":
            problems.append(f"índice de busca {name}: {st}")
    vitrine = db.economic_groups.find_one({"showcase": True}, {"applicant_id": 1})
    if not vitrine:
        problems.append("nenhum grupo de vitrine")
    else:
        t0 = time.perf_counter()
        out = list(db.companies.aggregate([
            {"$match": {"_id": vitrine["applicant_id"]}},
            {"$graphLookup": {"from": "ownership", "startWith": "$_id", "connectFromField": "owner_id",
                              "connectToField": "owned_id", "as": "acima", "maxDepth": 6}},
            {"$project": {"n": {"$size": "$acima"}}},
        ], maxTimeMS=15_000))
        ms = (time.perf_counter() - t0) * 1000
        print(f"  traversal de referência: {out[0]['n'] if out else 0} arestas acima em {ms:.0f} ms")
        if not out or out[0]["n"] == 0:
            problems.append("traversal de referência sem arestas")
    return problems


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--scale", choices=sorted(SCALES), default="full")
    p.add_argument("--drop", action="store_true", help="carga limpa (insert_many) em vez de upsert")
    p.add_argument("--skip-embeddings", action="store_true")
    p.add_argument("--search-timeout", type=int, default=900)
    args = p.parse_args()

    name = db_name()
    assert_write_allowed(name)  # antes de qualquer conexão ou escrita
    vol = {k: int(os.getenv(k, v)) for k, v in SCALES[args.scale].items()}
    env = {**os.environ, "MONGODB_DB": name, "PYTHONUNBUFFERED": "1"}
    py = sys.executable
    drop = ["--drop"] if args.drop else []
    print(f"banco: {name}  escala: {args.scale}  " + "  ".join(f"{k}={v:,}" for k, v in vol.items()))
    started = time.perf_counter()
    total = 8

    t = step(1, total, f"sócios pessoa física ({vol['PEOPLE']:,})")
    run([py, "data-generator/generate_people.py", "--people", str(vol["PEOPLE"]), *drop], env)
    done(t)

    t = step(2, total, f"base societária ({vol['COMPANIES']:,} empresas, {vol['ECON_GROUPS']:,} grupos)")
    run([py, "data-generator/generate_ownership.py", "--companies", str(vol["COMPANIES"]),
         "--groups", str(vol["ECON_GROUPS"]), "--showcase", str(vol["SHOWCASE"]), *drop], env)
    done(t)

    t = step(3, total, "hierarquia comercial")
    run([py, "data-generator/generate_advisors.py", *drop], env)
    done(t)

    t = step(4, total, "índices B-tree (mongosh schema/indexes.js)")
    run(["mongosh", os.environ["MONGODB_URI"], "--quiet", "schema/indexes.js"], env)
    done(t)

    db = get_db()
    t = step(5, total, "estado de revisão da apresentação")
    print("  ", reset_review_state(db))
    done(t)

    t = step(6, total, "vetores das atividades (Voyage)")
    if args.skip_embeddings or not os.getenv("VOYAGE_API_KEY"):
        print("  pulado: sem VOYAGE_API_KEY ou --skip-embeddings; o painel semântico ficará indisponível")
        if "activities" not in db.list_collection_names():
            db.create_collection("activities")
    else:
        run([py, "data-generator/embed_activities.py"], env)
    done(t)

    t = step(7, total, "Atlas Search e Vector Search (espera READY)")
    run([py, "schema/search_indexes.py", "--timeout", str(args.search_timeout)], env)
    done(t)

    t = step(8, total, "conferência")
    problems = verify(db)
    done(t)

    print(f"\ntotal: {time.perf_counter() - started:.0f} s")
    if problems:
        print("❌ reset incompleto:\n  - " + "\n  - ".join(problems))
        sys.exit(1)
    print(f"✅ {name} pronto para a demo")


if __name__ == "__main__":
    main()
