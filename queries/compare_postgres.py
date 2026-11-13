#!/usr/bin/env python3
"""MongoDB `$graphLookup` x PostgreSQL `WITH RECURSIVE`, mesma máquina, mesmo contrato.

Executa a primeira rodada do protocolo em `docs/comparison-protocol.md`, no recorte
que um notebook permite medir de forma equivalente:

- os dois motores rodam **locais, no mesmo hardware**, sem rede entre cliente e
  servidor (mongod e PostgreSQL embutido via `pgserver`), com 1 GB de cache cada;
- as mesmas fixtures entram nos dois: a curva da página de evidências (níveis
  1, 2, 4, 6; 1 a 3 filhos; hub de 1.200 filhas) e topologias hostis (ciclo,
  participação cruzada com sócio PF, profundidade insuficiente, raízes acima do
  teto, aresta para controlador inexistente), sobre uma base de fundo aleatória;
- o MongoDB executa a função versionada da POV (`ownership.economic_group`, uma
  agregação); o PostgreSQL executa **uma** consulta recursiva com as mesmas regras
  (subida, raízes reais, descida por raiz, alcance, hidratação e exposição);
- uma célula só entra na tabela de tempo se os dois devolverem o mesmo conjunto de
  empresas, as mesmas arestas, a mesma exposição em centavos e o mesmo alcance.

Não mede Atlas, rede, carga fria, escala de produção nem custo operacional: isso
fica declarado no JSON e na página. O resultado é uma fotografia local, não um
ranking de produtos.

    python3.12 -m venv /tmp/cmp && /tmp/cmp/bin/pip install -r queries/requirements-compare.txt
    /tmp/cmp/bin/python queries/compare_postgres.py --runs 100 --concurrency 1,8

Sem `--mongo-uri`/`--pg-uri`, sobe um `mongod` (precisa estar no PATH) e um
PostgreSQL embutido em diretórios temporários e encerra os dois no fim. Com URIs,
usa os servidores informados e grava só no banco `graph_compare_test`.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import platform
import random
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DB_NAME = "graph_compare_test"
OUT = ROOT / "frontend/public/evidence/comparison.json"
CACHE_GB = 1



# ---------------------------------------------------------------- fixtures --

def _company(cid, holding=False, name="Empresa de teste"):
    return {"_id": cid, "cnpj": cid, "razao_social": name, "is_holding": holding}


def _edge(owner, owned, kind="corporate", pct=100.0):
    return {"_id": f"{owner}>{owned}", "owner_id": owner, "owned_id": owned,
            "owner_type": kind, "percentage": pct, "qualificacao": "controlador" if pct > 50 else "socio"}


def _credit(cid, limite=100.0, vencido=0.0):
    return {"_id": cid, "company_id": cid, "limite": limite, "utilizado": limite / 2,
            "vencido": vencido, "rating": "A"}


def tree(depth, branching, prefix):
    """Mesma forma da curva de `build_evidence.py`: árvore cheia, consulta pela última folha."""
    companies = [_company(prefix + "_0", True, "Raiz de teste")]
    edges, frontier = [], [companies[0]["_id"]]
    for _ in range(depth):
        nxt = []
        for parent in frontier:
            for _ in range(branching):
                cid = f"{prefix}_{len(companies)}"
                companies.append(_company(cid))
                edges.append(_edge(parent, cid))
                nxt.append(cid)
        frontier = nxt
    credits = [_credit(c["_id"], vencido=10.0 if i == len(companies) - 1 else 0.0)
               for i, c in enumerate(companies)]
    return {"companies": companies, "edges": edges, "credits": credits, "people": [],
            "subject": companies[-1]["cnpj"]}


def hostile():
    """Topologias em que regra de raiz, ciclo e alcance costumam divergir entre motores."""
    out = {}
    # Ciclo corporativo: ninguém fica sem dono PJ, então não há raiz real.
    c = [_company(f"cic_{i}") for i in range(4)]
    out["ciclo"] = ({"companies": c, "people": [], "credits": [_credit(x["_id"]) for x in c],
                     "edges": [_edge("cic_0", "cic_1", pct=60), _edge("cic_1", "cic_2", pct=60),
                               _edge("cic_2", "cic_0", pct=60), _edge("cic_2", "cic_3")],
                     "subject": "cic_3"}, 4)
    # Duas holdings dividem a controlada; sócios PF no topo; irmã fora do caminho.
    c = [_company("cru_h1", True), _company("cru_h2", True)] + [_company(f"cru_{n}") for n in ("x", "y", "irma", "neta")]
    out["participacao_cruzada"] = ({"companies": c,
        "people": [{"_id": "cru_p1", "name": "Sócia Um"}, {"_id": "cru_p2", "name": "Sócio Dois"}],
        "credits": [_credit(x["_id"], vencido=5.0 if x["_id"] == "cru_neta" else 0.0) for x in c],
        "edges": [_edge("cru_h1", "cru_x", pct=60), _edge("cru_h2", "cru_x", pct=40), _edge("cru_x", "cru_y"),
                  _edge("cru_h2", "cru_irma"), _edge("cru_irma", "cru_neta"),
                  _edge("cru_p1", "cru_h1", "individual", 70), _edge("cru_p2", "cru_h2", "individual", 100)],
        "subject": "cru_y"}, 4)
    # Cadeia de 10 níveis consultada com profundidade 3: resposta parcial declarada.
    c = [_company(f"lon_{i}") for i in range(11)]
    out["profundidade_insuficiente"] = ({"companies": c, "people": [], "credits": [_credit(x["_id"]) for x in c],
        "edges": [_edge(f"lon_{i}", f"lon_{i + 1}") for i in range(10)], "subject": "lon_10"}, 3)
    # Sete controladoras diretas: acima do teto de raízes (5).
    c = [_company(f"rai_h{i}", True) for i in range(7)] + [_company("rai_alvo")]
    out["raizes_acima_do_teto"] = ({"companies": c, "people": [], "credits": [_credit(x["_id"]) for x in c],
        "edges": [_edge(f"rai_h{i}", "rai_alvo", pct=14.0) for i in range(7)], "subject": "rai_alvo"}, 2)
    # Sete controladoras, cada uma com uma descendência própria: quais cinco raízes
    # entram muda o conjunto devolvido, então a escolha precisa ser a mesma nos dois.
    c = ([_company(f"rad_h{i}", True) for i in range(7)] + [_company("rad_alvo")]
         + [_company(f"rad_f{i}") for i in range(7)])
    out["raizes_com_descendencias"] = ({"companies": c, "people": [], "credits": [_credit(x["_id"]) for x in c],
        "edges": [_edge(f"rad_h{i}", "rad_alvo", pct=14.0) for i in (3, 0, 6, 1, 5, 2, 4)]
                 + [_edge(f"rad_h{i}", f"rad_f{i}") for i in range(7)], "subject": "rad_alvo"}, 2)
    # Aresta para um controlador que não está no cadastro.
    c = [_company("orf_a"), _company("orf_b")]
    out["controlador_inexistente"] = ({"companies": c, "people": [], "credits": [_credit("orf_a")],
        "edges": [_edge("orf_fantasma", "orf_a"), _edge("orf_a", "orf_b")], "subject": "orf_b"}, 4)
    return out


def background(n_companies, seed=7):
    """Árvores aleatórias de fundo, para os índices não servirem só às fixtures."""
    rnd = random.Random(seed)
    companies, edges, credits, people = [], [], [], []
    while len(companies) < n_companies:
        g = len(companies)
        root = f"bg{g}_0"
        companies.append(_company(root, True))
        frontier, levels = [root], rnd.randint(1, 5)
        for _ in range(levels):
            nxt = []
            for parent in frontier:
                for _ in range(rnd.randint(1, 3)):
                    cid = f"bg{g}_{len(companies) - g}"
                    companies.append(_company(cid))
                    edges.append(_edge(parent, cid, pct=float(rnd.choice((51, 60, 100)))))
                    nxt.append(cid)
            frontier = nxt
        pid = f"bgp{g}"
        people.append({"_id": pid, "name": "Sócio de fundo"})
        edges.append(_edge(pid, root, "individual", 100.0))
    credits = [_credit(c["_id"], limite=float(rnd.randint(1, 900)) * 100) for c in companies if rnd.random() < 0.4]
    return {"companies": companies, "edges": edges, "credits": credits, "people": people}


def merge(*parts):
    out = {"companies": [], "edges": [], "credits": [], "people": []}
    for p in parts:
        for k in out:
            out[k].extend(p[k])
    return out


# ---------------------------------------------------------------- servers --

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def local_servers():
    """mongod e PostgreSQL embutido em diretórios temporários; ambos encerrados no fim."""
    import pgserver

    tmp = Path(tempfile.mkdtemp(prefix="graph_compare_"))
    port = _free_port()
    mongod = shutil.which("mongod")
    if not mongod:
        raise SystemExit("mongod não está no PATH; informe --mongo-uri")
    (tmp / "mongo").mkdir()
    proc = subprocess.Popen([mongod, "--dbpath", str(tmp / "mongo"), "--port", str(port), "--bind_ip", "127.0.0.1",
                             "--wiredTigerCacheSizeGB", str(CACHE_GB), "--quiet", "--logpath", str(tmp / "mongod.log")])
    pg = None
    try:
        pg = pgserver.get_server(str(tmp / "pg"), cleanup_mode="stop")
        pg.psql(f"ALTER SYSTEM SET shared_buffers = '{CACHE_GB}GB';")
        pg.cleanup()
        pg = pgserver.get_server(str(tmp / "pg"), cleanup_mode="stop")
        yield f"mongodb://127.0.0.1:{port}/?directConnection=true", pg.get_uri()
    finally:
        if pg is not None:
            pg.cleanup()
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ loading --

PG_SCHEMA = """
DROP TABLE IF EXISTS ownership, credit_exposure, people, companies CASCADE;
CREATE TABLE companies (id text PRIMARY KEY, cnpj text NOT NULL UNIQUE, razao_social text, is_holding boolean);
CREATE TABLE people (id text PRIMARY KEY, name text);
CREATE TABLE ownership (id text PRIMARY KEY, owner_id text NOT NULL, owned_id text NOT NULL,
                        owner_type text NOT NULL, percentage numeric(7,2), qualificacao text);
CREATE TABLE credit_exposure (company_id text PRIMARY KEY, limite numeric(18,2), utilizado numeric(18,2),
                              vencido numeric(18,2), rating text);
"""
# Os dois sentidos da relação, como no MongoDB (`owner_id_1`, `owned_id_1`).
PG_INDEXES = """
CREATE INDEX ownership_owner_id ON ownership (owner_id);
CREATE INDEX ownership_owned_id ON ownership (owned_id);
ANALYZE;
"""


def _copy(cur, table, cols, rows):
    buf = io.StringIO()
    for r in rows:
        buf.write("\t".join("\\N" if r.get(c) is None else str(r[c]) for c in cols) + "\n")
    buf.seek(0)
    with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
        cp.write(buf.read())


def load_pg(conn, data):
    t = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(PG_SCHEMA)
        _copy(cur, "companies", ["id", "cnpj", "razao_social", "is_holding"],
              [{**c, "id": c["_id"]} for c in data["companies"]])
        _copy(cur, "people", ["id", "name"], [{**p, "id": p["_id"]} for p in data["people"]])
        _copy(cur, "ownership", ["id", "owner_id", "owned_id", "owner_type", "percentage", "qualificacao"],
              [{**e, "id": e["_id"]} for e in data["edges"]])
        _copy(cur, "credit_exposure", ["company_id", "limite", "utilizado", "vencido", "rating"], data["credits"])
        cur.execute(PG_INDEXES)
        conn.commit()
        cur.execute("SELECT sum(pg_total_relation_size(c)) FROM unnest(ARRAY['companies','people','ownership','credit_exposure']::regclass[]) c")
        size = int(cur.fetchone()[0])
    return {"seconds": round(time.perf_counter() - t, 2), "bytes_data_and_indexes": size}


def load_mongo(db, data):
    t = time.perf_counter()
    for name in ("companies", "people", "ownership", "credit_exposure", "advisors"):
        db.drop_collection(name)
    db.companies.insert_many(data["companies"], ordered=False)
    if data["people"]:
        db.people.insert_many(data["people"], ordered=False)
    db.ownership.insert_many(data["edges"], ordered=False)
    db.credit_exposure.insert_many(data["credits"], ordered=False)
    # Os mesmos índices de `schema/indexes.js` que o traversal usa.
    db.companies.create_index("cnpj", unique=True)
    db.ownership.create_index("owner_id")
    db.ownership.create_index("owned_id")
    db.credit_exposure.create_index("company_id", unique=True)
    db.client.admin.command("fsync")  # checkpoint antes de ler tamanho em disco
    st = db.command("dbStats")
    return {"seconds": round(time.perf_counter() - t, 2),
            "bytes_data_and_indexes": int(st["storageSize"] + st["indexSize"])}


# --------------------------------------------------------------- PostgreSQL --

# Uma consulta, as mesmas regras de `ownership._pipeline_grupo`:
#   subida com `nivel` 0..d; raízes = {consultada} ∪ donos PJ − controladas por PJ;
#   no máximo `max_roots`; raiz com dono PJ no banco não é raiz; descida por raiz;
#   alcance pela fronteira (existe aresta além do último nível não percorrida?).
# `UNION` sobre (aresta, nível) termina em ciclos e não explode em DAG: no máximo
# arestas × (d + 1) linhas, como a visita em largura do `$graphLookup`.
PG_GROUP_SQL = """
WITH RECURSIVE
subj AS (SELECT id, cnpj, razao_social FROM companies WHERE cnpj = %(cnpj)s LIMIT 1),
up AS (
  SELECT o.id AS eid, o.owner_id, 0 AS nivel FROM ownership o JOIN subj s ON o.owned_id = s.id
  UNION
  SELECT o.id, o.owner_id, u.nivel + 1 FROM up u JOIN ownership o ON o.owned_id = u.owner_id
  WHERE u.nivel < %(d)s
),
up_e AS (SELECT o.*, m.nivel FROM (SELECT eid, min(nivel) AS nivel FROM up GROUP BY eid) m
         JOIN ownership o ON o.id = m.eid),
cand AS ((SELECT id FROM subj UNION SELECT owner_id FROM up_e WHERE owner_type = 'corporate')
         EXCEPT SELECT owned_id FROM up_e WHERE owner_type = 'corporate'),
cand2 AS (SELECT id FROM cand UNION ALL SELECT id FROM subj WHERE NOT EXISTS (SELECT 1 FROM cand)),
roots_lim AS (SELECT id FROM cand2 ORDER BY id LIMIT %(max_roots)s),
com_dono AS (SELECT DISTINCT r.id FROM roots_lim r
             JOIN ownership o ON o.owned_id = r.id AND o.owner_type = 'corporate'),
real_roots AS (SELECT id FROM roots_lim EXCEPT SELECT id FROM com_dono),
roots AS (SELECT id FROM real_roots UNION ALL SELECT id FROM subj WHERE NOT EXISTS (SELECT 1 FROM real_roots)),
down AS (
  SELECT r.id AS root, o.id AS eid, o.owned_id, 0 AS nivel
  FROM roots r JOIN companies c ON c.id = r.id JOIN ownership o ON o.owner_id = r.id
  UNION
  SELECT d.root, o.id, o.owned_id, d.nivel + 1 FROM down d JOIN ownership o ON o.owner_id = d.owned_id
  WHERE d.nivel < %(d)s
),
down_e AS (SELECT root, eid, min(nivel) AS nivel, min(owned_id) AS owned_id FROM down GROUP BY root, eid),
edges AS (SELECT o.owner_id, o.owned_id, o.owner_type, o.percentage, o.qualificacao FROM ownership o
          WHERE o.id IN (SELECT id FROM up_e UNION SELECT eid FROM down_e)),
ids_emp AS (SELECT id FROM subj UNION SELECT owned_id FROM edges
            UNION SELECT owner_id FROM edges WHERE owner_type = 'corporate'),
ids_pes AS (SELECT DISTINCT owner_id AS id FROM edges WHERE owner_type = 'individual')
SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM subj) THEN NULL ELSE json_build_object(
  'subject', (SELECT id FROM subj),
  'edges', (SELECT coalesce(json_agg(e), '[]') FROM edges e),
  'companies', (SELECT coalesce(json_agg(json_build_object(
                  'id', c.id, 'cnpj', c.cnpj, 'razao_social', c.razao_social, 'is_holding', c.is_holding,
                  'limite', ce.limite, 'utilizado', ce.utilizado, 'vencido', ce.vencido, 'rating', ce.rating)), '[]')
                FROM ids_emp i JOIN companies c ON c.id = i.id LEFT JOIN credit_exposure ce ON ce.company_id = c.id),
  'people', (SELECT coalesce(json_agg(json_build_object('id', p.id, 'name', p.name)), '[]')
             FROM ids_pes i JOIN people p ON p.id = i.id),
  'missing', (SELECT count(*) FROM ids_emp i WHERE NOT EXISTS (SELECT 1 FROM companies c WHERE c.id = i.id))
           + (SELECT count(*) FROM ids_pes i WHERE NOT EXISTS (SELECT 1 FROM people p WHERE p.id = i.id)),
  'edges_limited_up', (SELECT count(*) > %(teto)s FROM up_e),
  'edges_limited_down', EXISTS (SELECT 1 FROM down_e GROUP BY root HAVING count(*) > %(teto)s),
  'edges_limited_total', (SELECT count(*) FROM up_e) + (SELECT count(*) FROM down_e) > %(teto)s,
  'depth_limited_up', EXISTS (SELECT 1 FROM up_e f JOIN ownership o ON o.owned_id = f.owner_id
                              WHERE f.nivel = %(d)s AND o.id NOT IN (SELECT id FROM up_e)),
  'depth_limited_down', EXISTS (SELECT 1 FROM down_e f JOIN ownership o ON o.owner_id = f.owned_id
                                WHERE f.nivel = %(d)s AND NOT EXISTS (
                                  SELECT 1 FROM down_e s WHERE s.root = f.root AND s.eid = o.id)),
  'roots_limited', (SELECT count(*) > %(max_roots)s FROM cand2),
  'root_unresolved', EXISTS (SELECT 1 FROM com_dono)
) END
"""

FLAGS = ("edges_limited_up", "edges_limited_down", "edges_limited_total",
         "depth_limited_up", "depth_limited_down", "roots_limited", "root_unresolved")


def pg_group(conn, cnpj, depth, teto, max_roots):
    with conn.cursor() as cur:
        cur.execute(PG_GROUP_SQL, {"cnpj": cnpj, "d": depth, "teto": teto, "max_roots": max_roots})
        doc = cur.fetchone()[0]
    if doc is None:
        return {"found": False}
    reasons = [k for k in FLAGS if doc[k]]
    if doc["missing"]:
        reasons.append("missing_entities")
    companies = doc["companies"]
    # Mesma regra da resposta da POV: aresta cujo extremo não existe no cadastro
    # sai do desenho, e o motivo `missing_entities` declara a lacuna.
    known = {c["id"] for c in companies} | {p["id"] for p in doc["people"]}
    return {
        "found": True,
        "companies": sorted(c["id"] for c in companies),
        "edges": sorted({(e["owner_id"], e["owned_id"], e["owner_type"], float(e["percentage"])) for e in doc["edges"]
                         if e["owner_id"] in known and e["owned_id"] in known}),
        "exposure_cents": {k: sum(_cents(c[k] or 0) for c in companies) for k in ("limite", "utilizado", "vencido")},
        "reasons": sorted(reasons),
    }


# ------------------------------------------------------------------ MongoDB --

def _cents(v):
    return int((Decimal(str(v)) * 100).quantize(Decimal("1")))


def mongo_raw(db, ownership, cnpj, depth, teto):
    """Mesma pipeline versionada, com o pós-processamento mínimo que o lado SQL também faz.

    É este caminho que entra na cronometragem: `economic_group` monta a resposta
    completa da tela (nós com assessor, ordenação, recorte), trabalho de cliente
    que o lado PostgreSQL não faz. Cronometrar um contra o outro mediria Python.
    """
    from app.db.client import bounded_aggregate
    docs = bounded_aggregate(db.companies, ownership._pipeline_grupo(cnpj, depth, teto, ownership.MAX_RAIZES))
    if not docs:
        return {"found": False}
    doc = docs[0]
    empresas = {c["_id"] for c in doc["empresas"]} | {doc["_id"]}
    pessoas = {p["_id"] for p in doc["pessoas"]}
    known = empresas | pessoas
    reasons = [k for k in FLAGS if doc.get(k)]
    if set(doc["ids_empresa"]) - empresas or set(doc["ids_pessoa"]) - pessoas:
        reasons.append("missing_entities")
    creditos = [c for c in doc["creditos"] if c["company_id"] in empresas]
    return {
        "found": True,
        "companies": sorted(empresas),
        "edges": sorted({(e["owner_id"], e["owned_id"], e["owner_type"], float(e["percentage"])) for e in doc["arestas"]
                         if e["owner_id"] in known and e["owned_id"] in known}),
        "exposure_cents": {k: sum(_cents(c[k]) for c in creditos) for k in ("limite", "utilizado", "vencido")},
        "reasons": sorted(reasons),
    }


def mongo_contract(result):
    if not result.get("found"):
        return {"found": False}
    companies = [n for n in result["nodes"] if n["kind"] == "company"]
    return {
        "found": True,
        "companies": sorted(n["id"] for n in companies),
        "edges": sorted({(e["from"], e["to"], e["type"], float(e["percentage"])) for e in result["edges"]}),
        "exposure_cents": {k: sum(_cents(n[k]) for n in companies) for k in ("limite", "utilizado", "vencido")},
        "reasons": sorted(r for r in result["coverage"]["reasons"] if r != "node_limit"),
    }


# -------------------------------------------------------------- measurement --

def pct(values, q):
    v = sorted(values)
    return round(v[max(0, math.ceil(len(v) * q) - 1)], 3)


def summarize(samples):
    return {"n": len(samples), "p50_ms": round(statistics.median(samples), 3), "p95_ms": pct(samples, .95),
            "p99_ms": pct(samples, .99), "max_ms": round(max(samples), 3),
            "samples_ms": [round(v, 3) for v in samples]}


def timed(fn):
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000


WARMUP_PER_WORKER = 10


def run_cell(engines, concurrency, runs):
    """Amostras concluídas por motor; a ordem dos motores alterna a cada rodada."""
    out = {name: [] for name in engines}
    per_worker = math.ceil(runs / concurrency)

    def worker(seed):
        local = {name: [] for name in engines}
        names = list(engines)
        # Aquecimento em cada thread, fora da distribuição: abre a conexão
        # PostgreSQL desta thread e passa do limiar de prepared statement
        # (prepare_threshold=5) antes de medir; o MongoDB recebe o mesmo tratamento.
        for _ in range(WARMUP_PER_WORKER):
            for name in names:
                engines[name]()
        for i in range(per_worker):
            order = names if (i + seed) % 2 == 0 else names[::-1]
            for name in order:
                local[name].append(timed(engines[name]))
        return local

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        for local in ex.map(worker, range(concurrency)):
            for name, v in local.items():
                out[name].extend(v)
    return {name: summarize(v) for name, v in out.items()}


def mongo_server_ms(db, ownership, cnpj, depth, teto):
    pipeline = ownership._pipeline_grupo(cnpj, depth, teto, ownership.MAX_RAIZES)
    doc = db.command("explain", {"aggregate": "companies", "pipeline": pipeline, "cursor": {}},
                     verbosity="executionStats")
    # A estimativa por estágio é cumulativa; o maior valor cobre a pipeline inteira.
    # `executionStats.executionTimeMillis` do topo mede só o `$cursor` inicial.
    estimates = [st.get("executionTimeMillisEstimate") for st in doc.get("stages", [])]
    estimates = [int(v) for v in estimates if v is not None]
    return {"ms": max(estimates) if estimates else None, "resolution": "1 ms (estimativa do servidor)"}


def pg_server_ms(conn, cnpj, depth, teto, max_roots):
    with conn.cursor() as cur:
        cur.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + PG_GROUP_SQL,
                    {"cnpj": cnpj, "d": depth, "teto": teto, "max_roots": max_roots})
        plan = cur.fetchone()[0][0]
    return {"ms": round(plan["Execution Time"], 3), "planning_ms": round(plan["Planning Time"], 3),
            "jit_used": "JIT" in plan}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--runs", type=int, default=100, help="amostras concluídas por motor e célula")
    p.add_argument("--concurrency", default="1,8")
    p.add_argument("--background", type=int, default=100_000, help="empresas de fundo")
    p.add_argument("--mongo-uri")
    p.add_argument("--pg-uri")
    p.add_argument("--out", default=str(OUT))
    a = p.parse_args()
    if a.runs < 20:
        p.error("use ao menos 20 amostras por célula")
    levels = [int(x) for x in a.concurrency.split(",")]

    if bool(a.mongo_uri) != bool(a.pg_uri):
        p.error("informe --mongo-uri e --pg-uri juntos, ou nenhum dos dois")
    if a.mongo_uri:
        # O script recria tabelas: só aceita um banco PostgreSQL descartável.
        from urllib.parse import urlparse
        pg_db = urlparse(a.pg_uri).path.lstrip("/")
        if not pg_db.endswith("_test"):
            p.error(f"o banco PostgreSQL precisa terminar em _test (recebido: {pg_db or 'vazio'})")
        ctx = contextmanager(lambda: (yield (a.mongo_uri, a.pg_uri)))()
        environment = "servidores informados por URI"
    else:
        ctx = local_servers()
        environment = (f"mongod e PostgreSQL locais no mesmo computador ({platform.machine()}, "
                       f"{os.cpu_count()} CPUs), cliente na mesma máquina, sem rede; {CACHE_GB} GB de cache em cada motor")

    with ctx as (mongo_uri, pg_uri):
        os.environ["MONGODB_URI"] = mongo_uri
        os.environ["MONGODB_DB"] = DB_NAME
        # Teto de exibição fora do caminho: o contrato comparado é o traversal, não o
        # recorte de nós que a tela faz em Python depois da consulta.
        os.environ["GRAPH_MAX_NODES"] = "100000"
        sys.path.insert(0, str(ROOT / "backend"))
        import psycopg
        from pymongo import MongoClient
        from app.config import get_settings
        from app.db import ownership

        s = get_settings()
        teto = s.max_nodes * 3
        mclient = MongoClient(mongo_uri)
        mdb = mclient[DB_NAME]
        pg_conn_args = {"conninfo": pg_uri, "autocommit": False}

        cells = [(f"arvore_{d}n_{b}f", tree(d, b, f"t{d}x{b}"), d) for b in (1, 2, 3) for d in (1, 2, 4, 6)]
        cells.append(("hub_1200", tree(1, 1200, "hub"), 1))
        cells += [(name, fx, d) for name, (fx, d) in hostile().items()]
        data = merge(background(a.background), *[fx for _, fx, _ in cells])

        result = {
            "schema_version": 1,
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "status": "measured_locally",
            "environment": environment,
            "engines": {"mongodb": mclient.server_info()["version"]},
            "dataset": {"companies": len(data["companies"]), "edges": len(data["edges"]),
                        "credits": len(data["credits"]), "people": len(data["people"]),
                        "background_companies": a.background},
            "method": {
                "mongodb": "ownership.economic_group: uma agregação ($graphLookup subida + $lookup/$graphLookup descida + hidratação)",
                "postgresql": "uma consulta WITH RECURSIVE (UNION sobre aresta e nível), mesmas regras de raiz e alcance",
                "timing": "ponta a ponta no cliente Python: uma consulta e o mesmo pós-processamento mínimo nos dois lados; aquecido; ordem dos motores alternada",
                "correctness": "mesmas empresas, arestas (origem, destino, tipo, %), exposição em centavos e motivos de alcance",
                "server_ms": "uma execução com explain executionStats (MongoDB) e EXPLAIN ANALYZE (PostgreSQL)",
                "runs_per_cell": a.runs, "concurrency": levels,
            },
            "not_measured": [
                "Atlas ou PostgreSQL gerenciado, nem rede entre cliente e servidor",
                "a versão da demonstração: aqui roda o mongod local informado em engines.mongodb, não o Atlas 9.0 da demo",
                "carga fria (cache vazio)",
                "base de 1,2 milhão de empresas da demonstração",
                "concorrências 32 e 64 do protocolo",
                "custo operacional, atualização de relações e algoritmos de grafo",
                "revisão dos planos por especialistas de cada motor",
            ],
        }

        with psycopg.connect(**pg_conn_args) as conn:
            result["engines"]["postgresql"] = conn.execute("SHOW server_version").fetchone()[0]
            result["engines"]["postgresql_settings"] = {k: conn.execute(f"SHOW {k}").fetchone()[0]
                                                        for k in ("shared_buffers", "work_mem", "jit")}
            result["engines"]["mongodb_cache_gb"] = mclient.admin.command(
                "serverStatus")["wiredTiger"]["cache"]["maximum bytes configured"] / 2**30
            print("carga PostgreSQL…", flush=True)
            result["load"] = {"postgresql": load_pg(conn, data)}
        print("carga MongoDB…", flush=True)
        result["load"]["mongodb"] = load_mongo(mdb, data)

        # Outros processos na mesma máquina distorcem a cauda (sobretudo com 8
        # clientes); a carga fica registrada para quem ler o JSON.
        result["host_load_average"] = {"start": [round(v, 1) for v in os.getloadavg()]}
        local = {}

        def pg_conn():
            # Uma conexão por thread: psycopg não compartilha conexão entre threads com segurança.
            import threading
            key = threading.get_ident()
            if key not in local:
                local[key] = psycopg.connect(**pg_conn_args)
            return local[key]

        rows = []
        with patch.object(ownership, "get_db", return_value=mdb):
            for name, fx, depth in cells:
                cnpj = fx["subject"]
                # Correção pela função de produto e pelo caminho cronometrado.
                m = mongo_contract(ownership.economic_group(cnpj, depth))
                raw = mongo_raw(mdb, ownership, cnpj, depth, teto)
                g = pg_group(pg_conn(), cnpj, depth, teto, ownership.MAX_RAIZES)
                same = m == g == raw
                diff = {} if same else {k: {"mongodb": m.get(k), "postgresql": g.get(k)}
                                        for k in set(m) | set(g) if m.get(k) != g.get(k)}
                row = {"cell": name, "depth": depth, "fixture_companies": len(fx["companies"]),
                       "fixture_edges": len(fx["edges"]), "returned_companies": len(m.get("companies", [])),
                       "returned_edges": len(m.get("edges", [])), "reasons": m.get("reasons", []),
                       "complete": not m.get("reasons"), "same_result": same, "divergence": diff}
                if same:
                    engines = {"mongodb": lambda: mongo_raw(mdb, ownership, cnpj, depth, teto),
                               "postgresql": lambda: pg_group(pg_conn(), cnpj, depth, teto, ownership.MAX_RAIZES)}
                    for _ in range(5):  # aquecimento fora da distribuição
                        for fn in engines.values():
                            fn()
                    row["latency"] = {str(c): run_cell(engines, c, a.runs) for c in levels}
                    row["server_ms"] = {"mongodb": mongo_server_ms(mdb, ownership, cnpj, depth, teto),
                                        "postgresql": pg_server_ms(pg_conn(), cnpj, depth, teto, ownership.MAX_RAIZES)}
                rows.append(row)
                lat = row.get("latency", {}).get(str(levels[0]), {})
                print(f"{name:28} igual={same} " + " ".join(
                    f"{k}:p50={v['p50_ms']}ms p95={v['p95_ms']}ms" for k, v in lat.items()), flush=True)
        for c in local.values():
            c.close()
        mclient.drop_database(DB_NAME)
        mclient.close()

    result["host_load_average"]["end"] = [round(v, 1) for v in os.getloadavg()]
    result["cells"] = rows
    result["passed"] = all(r["same_result"] for r in rows)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print("salvo", a.out, "resultados idênticos em todas as células:", result["passed"], flush=True)
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
