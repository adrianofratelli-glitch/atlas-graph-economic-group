"""Entradas hostis contra a API em execução (8350). Somente leitura no banco da demo.

Uso: ./start.sh  (em outro terminal) e depois
     backend/venv/bin/python tests/http_adversarial.py

Amplia `test_resilience.py` (46 checagens) com o que a revisão de 2026-10
reproduziu contra o servidor real: ids gigantes no corpo, campos extras,
unicode/RTL/zero-width/NUL, operadores do Mongo no caminho e no corpo, CORS de
origem estranha e o esgotamento do threadpool por conexões SSE.
Resultado em `tests/http-adversarial-results.json`.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import httpx

BASE = os.getenv("API_BASE", "http://127.0.0.1:8350")
c = httpx.Client(base_url=BASE, timeout=60)
checks: list[dict] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    checks.append({"test": label, "passed": bool(cond), "detail": detail})
    print(("PASS " if cond else "FAIL ") + label + (f"  — {detail}" if detail else ""), flush=True)


def status(label: str, resp: httpx.Response, expected: set[int]) -> None:
    check(label, resp.status_code in expected and "mongodb.net" not in resp.text, f"HTTP {resp.status_code}")


def sse_starvation(n: int) -> float:
    stop = threading.Event()

    def hold():
        try:
            with httpx.stream("GET", BASE + "/api/alerts/stream", timeout=60) as r:
                for _ in r.iter_raw():
                    if stop.is_set():
                        break
        except httpx.HTTPError:
            pass

    threads = [threading.Thread(target=hold, daemon=True) for _ in range(n)]
    for t in threads:
        t.start()
    time.sleep(3)
    t0 = time.perf_counter()
    c.get("/health/live", timeout=30)
    ms = (time.perf_counter() - t0) * 1000
    stop.set()
    return ms


def main() -> None:
    cnpj = c.get("/api/entry-points").json()["applicants"][0]["cnpj"]

    status("CNPJ de 10 mil caracteres vira 404", c.get("/api/group/" + "9" * 10_000), {404})
    for label, raw in [("zero-width", "​" + cnpj), ("RTL + emoji", "‮🔥" + cnpj),
                       ("NUL", "%00"), ("só espaços", "%20%20"), ("$where", '{"$where":"sleep(5000)"}'),
                       ("$gt", '{"$gt":""}')]:
        status(f"CNPJ {label} não casa nada (404)", c.get("/api/group/" + raw), {404})
    status("path traversal não sai da rota", c.get("/api/group/..%2F..%2Fetc%2Fpasswd"), {404})
    status("depth não numérico é 422", c.get(f"/api/group/{cnpj}?depth=abc"), {422})
    status("depth fracionário é 422", c.get(f"/api/group/{cnpj}?depth=1.5"), {422})
    r = c.get(f"/api/group/{cnpj}?depth=99999999999999999999999")
    check("depth gigante é limitado ao cap", r.status_code == 200 and r.json()["depth"] <= 6, f"HTTP {r.status_code}")
    r = c.get(f"/api/group/{cnpj}?depth=0")
    check("depth zero vira 1", r.status_code == 200 and r.json()["depth"] == 1, f"HTTP {r.status_code}")

    status("concentração com 2.000 ids de 10 KB é 422, não 500",
           c.post("/api/analysis/concentration", json={"company_ids": ["x" * 10_000] * 2000}), {422})
    status("concentração com operador no id é 422",
           c.post("/api/analysis/concentration", json={"company_ids": [{"$gt": ""}]}), {422})
    status("concentração com campo extra é 422",
           c.post("/api/analysis/concentration", json={"company_ids": ["company_a"], "$where": "1"}), {422})
    t0 = time.perf_counter()
    r = c.post("/api/search/companies", json={"q": "Ltda", "node_ids": ["n" * 1000] * 4000})
    status("busca com 4.000 node_ids de 1 KB é 422", r, {422})
    check("recusa do corpo gigante é imediata", (time.perf_counter() - t0) < 2, f"{(time.perf_counter()-t0)*1000:.0f} ms")
    status("busca com campo extra é 422", c.post("/api/search/companies", json={"q": "Ltda", "$where": "1"}), {422})
    status("busca com q objeto é 422", c.post("/api/search/companies", json={"q": {"$gt": ""}}), {422})
    status("JSON malformado é 422", c.post("/api/search/companies", content=b'{"q": "a",',
                                            headers={"content-type": "application/json"}), {422})
    status("q de 1 MB é 422", c.post("/api/search/companies", json={"q": "a" * 1_000_000}), {422})
    for label, q in [("zero-width", "​​"), ("RTL + emoji", "‮🔥 Ltda"), ("CJK", "株式会社")]:
        status(f"busca {label} responde sem 500", c.post("/api/search/companies", json={"q": q}), {200, 503})
    status("case_id com operador é 404", c.get("/api/credit/case/" + '{"$ne":null}'), {404})
    status("token de revisão forjado é 409", c.post("/api/credit/review", json={"investigation_token": "a" * 50}), {409})
    status("portfolio com limit gigante é 422", c.get("/api/hierarchy/x/portfolio?limit=1000000"), {422})
    r = c.options("/api/demo/reset", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    check("CORS recusa origem estranha", "access-control-allow-origin" not in r.headers, f"HTTP {r.status_code}")

    ms = sse_starvation(45)
    check("45 conexões SSE não travam a API (/health/live < 1 s)", ms < 1000, f"{ms:.0f} ms")

    Path(__file__).with_name("http-adversarial-results.json").write_text(
        json.dumps({"base": BASE, "checks": checks}, indent=2, ensure_ascii=False))
    failed = [x["test"] for x in checks if not x["passed"]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passaram")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
