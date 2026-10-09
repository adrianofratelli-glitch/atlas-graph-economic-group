"""Reset da demo concorrente com abertura/fechamento de revisão, no Atlas real.

Uso: backend/venv/bin/python tests/live_reset_race.py

Regressão do achado GR-01 (2026-10-08): `reset_all()` pausado entre a limpeza de
`companies` e o `delete_many` de `credit_decisions` deixava uma abertura comitar
no meio e o grupo ficava bloqueado sem caso (43 empresas órfãs no banco small).
Cria `graph_resetrace_test_<uuid>`, nunca toca o banco da demo e remove o banco
criado mesmo quando falha. O agendamento só controla o instante entre chamadas
reais ao MongoDB; nenhum retorno do banco é fabricado.
"""
import os
import random
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

name = "graph_resetrace_test_" + uuid.uuid4().hex
os.environ["MONGODB_DB"] = name
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient  # noqa: E402

from app.db import credit_decision as cd, ownership  # noqa: E402
from app.db.client import get_client, get_db  # noqa: E402
from app.services import investigation  # noqa: E402
from app.services.alerts import hub  # noqa: E402
from main import app  # noqa: E402

checks: list[dict] = []
COMPANIES = ["root", "a", "b", "c"]


def check(label: str, condition: bool, detail: str = "") -> None:
    checks.append({"test": label, "passed": bool(condition)})
    print(("PASS " if condition else "FAIL ") + label + (f"  — {detail}" if detail else ""), flush=True)


def seed(db) -> None:
    assert db.name == name and name.startswith("graph_resetrace_test_")
    db.companies.insert_many([{"_id": "company_" + x, "cnpj": x, "razao_social": x, "advisor_id": "advisor_a",
                               "is_holding": x == "root", "credit_status": "active"} for x in COMPANIES])
    db.people.insert_one({"_id": "person_p", "name": "P"})
    db.advisors.insert_one({"_id": "advisor_a", "nome": "A", "reports_to": None, "papel": "assessor"})
    db.ownership.insert_many([{"_id": str(i), "owner_id": o, "owned_id": c, "owner_type": k, "percentage": 100}
                              for i, (o, c, k) in enumerate([
                                  ("person_p", "company_root", "individual"), ("company_root", "company_a", "corporate"),
                                  ("company_root", "company_b", "corporate"), ("company_b", "company_c", "corporate")])])
    db.credit_exposure.insert_many([{"_id": x, "company_id": "company_" + x, "advisor_id": "advisor_a",
                                     "limite": 100, "utilizado": 50, "vencido": 0, "rating": "A"} for x in COMPANIES])
    db.companies.create_index("cnpj", unique=True)
    db.ownership.create_index("owned_id")
    db.ownership.create_index("owner_id")
    db.credit_exposure.create_index("company_id", unique=True)


def orphans(db) -> int:
    """Empresas/exposições marcadas cujo caso não existe ou não está aberto."""
    open_cases = {c["_id"] for c in db.credit_decisions.find({"status": "open"}, {"_id": 1})}
    bad = 0
    for coll, flag in ((db.companies, {"credit_status": "under_review"}), (db.credit_exposure, {"review_flag": True})):
        for doc in coll.find(flag, {"case_id": 1}):
            bad += doc.get("case_id") not in open_cases
    return bad


def token():
    return investigation.issue(ownership.economic_group("c", 6))["token"]


def paused_reset_then_open(db) -> None:
    """O agendamento do juiz: reset pausa depois de liberar companies; abertura roda inteira."""
    real = db
    paused, resume, result = threading.Event(), threading.Event(), {}

    class Companies:
        def __getattr__(self, n): return getattr(real.companies, n)

        def update_many(self, *a, **kw):
            r = real.companies.update_many(*a, **kw)
            if "session" not in kw and not paused.is_set():
                paused.set(); resume.wait(30)
            return r

    class DB:
        companies = Companies()
        def __getattr__(self, n): return getattr(real, n)

    cd.reset_all()
    tok = token()
    with patch.object(cd, "get_db", return_value=DB()):
        t = threading.Thread(target=lambda: result.update(reset=cd.reset_all())); t.start()
        check("reset pausado no meio", paused.wait(10))
        opened = cd.open_review(tok, "abertura durante reset")
        resume.set(); t.join(30)
    check("abertura recusada enquanto o reset roda", opened.get("ok") is False and opened.get("reset_in_progress") is True,
          str({k: opened.get(k) for k in ("ok", "reset_in_progress", "error")}))
    check("nenhuma empresa marcada sem caso (agendamento do juiz)", orphans(real) == 0, f"órfãs={orphans(real)}")
    check("lease liberado no fim do reset", real.demo_control.find_one({"_id": cd.CONTROL_ID})["reset_until"] is None)
    again = cd.open_review(token(), "depois do reset")
    check("abertura volta a funcionar após o reset", again.get("ok") is True and again.get("companies") == 4)
    cd.reset_all()


def paused_open_then_reset(db) -> None:
    """Ordem inversa: transação de abertura em voo quando o reset começa."""
    cd.reset_all()
    tok = token()
    inside, result = threading.Event(), {}
    real_group = ownership.economic_group

    def slow_group(*a, **kw):
        g = real_group(*a, **kw)
        if not inside.is_set():
            inside.set(); time.sleep(1.5)  # transação já escreveu no documento de controle
        return g

    with patch.object(ownership, "economic_group", side_effect=slow_group):
        t = threading.Thread(target=lambda: result.update(opened=cd.open_review(tok, "abertura antes do reset"))); t.start()
        check("transação de abertura em voo", inside.wait(10))
        reset = cd.reset_all()
        t.join(30)
    opened = result.get("opened", {})
    check("abertura comitou antes do reset", opened.get("ok") is True, str(opened.get("error")))
    check("reset limpou o caso comitado junto", reset["companies_restored"] == 4 and db.credit_decisions.count_documents({}) == 0,
          str(reset))
    check("nenhuma empresa marcada sem caso (ordem inversa)", orphans(db) == 0)


def close_during_reset(db) -> None:
    cd.reset_all()
    case = cd.open_review(token(), "fechar durante reset")["case_id"]
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    db.demo_control.update_one({"_id": cd.CONTROL_ID}, {"$set": {"reset_until": future, "reset_owner": "outro"}})
    try:
        out = cd.close_review(case)
        check("fechamento recusado enquanto o reset roda", out.get("reset_in_progress") is True)
        with TestClient(app, raise_server_exceptions=False) as api:
            r = api.post("/api/credit/close/" + case)
            check("API devolve 409 no fechamento durante reset", r.status_code == 409 and r.json()["detail"]["reset_in_progress"])
            r = api.post("/api/credit/review", json={"investigation_token": token()})
            check("API devolve 409 na abertura durante reset", r.status_code == 409 and r.json()["detail"]["reset_in_progress"])
            with patch.object(cd, "RESET_WAIT_S", 0.5):
                r = api.post("/api/demo/reset")
            check("segundo reset com lease alheio vivo devolve 409", r.status_code == 409)
    finally:
        db.demo_control.update_one({"_id": cd.CONTROL_ID}, {"$set": {"reset_until": None}, "$unset": {"reset_owner": ""}})
    check("fechamento funciona depois", cd.close_review(case).get("ok") is True)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.demo_control.update_one({"_id": cd.CONTROL_ID}, {"$set": {"reset_until": past, "reset_owner": "morto"}})
    out = cd.open_review(token(), "lease expirado")
    check("lease expirado (processo morto) não trava a demo", out.get("ok") is True)
    check("reset toma lease expirado", cd.reset_all()["ok"])


def random_interleavings(db, rounds: int = 12) -> None:
    rng = random.Random(20261008)
    worst = 0
    for i in range(rounds):
        tok = token()

        def opener():
            time.sleep(rng.random() * 0.4)
            try:
                out = cd.open_review(tok, f"rodada {i}")
                if out.get("ok") and rng.random() < 0.5:
                    cd.close_review(out["case_id"])
            except investigation.InvestigationError:
                pass  # comprovante de caso já encerrado: comportamento esperado

        def resetter():
            time.sleep(rng.random() * 0.4)
            cd.reset_all()

        threads = [threading.Thread(target=f) for f in (opener, opener, resetter, resetter)]
        for t in threads: t.start()
        for t in threads: t.join(60)
        worst = max(worst, orphans(db))
        cd.reset_all()
    check(f"{rounds} rodadas aleatórias abertura/fechamento/reset sem órfãs", worst == 0, f"pior={worst}")


def run() -> None:
    db = get_db()
    seed(db)
    paused_reset_then_open(db)
    paused_open_then_reset(db)
    close_during_reset(db)
    random_interleavings(db)


if __name__ == "__main__":
    try:
        run()
    finally:
        hub.stop()
        if hub._thread: hub._thread.join(timeout=5)
        assert get_db().name == name and name.startswith("graph_resetrace_test_")
        get_client().drop_database(name)
        print("banco efêmero removido:", name, flush=True)
    failed = [c for c in checks if not c["passed"]]
    print(f"{len(checks) - len(failed)}/{len(checks)} checagens", flush=True)
    raise SystemExit(1 if failed else 0)
