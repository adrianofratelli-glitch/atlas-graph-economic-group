"""Decisão de crédito sobre o grupo econômico inteiro, em uma transação ACID.

## O argumento

Ou o grupo inteiro entra em revisão, ou nenhuma empresa entra. Um estado
intermediário — metade das empresas do grupo bloqueada para novas operações,
metade liberada, sem registro de decisão coerente — é pior do que não ter
decidido: a mesa de crédito aprova pela porta que ficou aberta, e a auditoria
depois não consegue reconstruir o que foi decidido nem quando.

Esse é exatamente o cenário que uma escrita não-atômica produz, e é por isso que
o escopo da transação cobre as três escritas juntas:

- `companies.credit_status` — o bloqueio operacional que a esteira de crédito lê;
- `credit_exposure.review_flag` — a marca na exposição, que os relatórios leem;
- um documento em `credit_decisions` — o registro de auditoria.

`readConcern: snapshot` e `writeConcern: majority`: a decisão é tomada sobre uma
fotografia consistente do grupo, e só é considerada tomada quando a maioria do
conjunto de réplicas confirmou.

## Por que recusa um segundo caso

Abrir duas revisões sobre as mesmas empresas sobrescreveria o `case_id` e deixaria
a primeira como casca: aberta, sem empresa nenhuma apontando para ela. Para um
processo de crédito isso é pior do que um erro — e recusar também é o
comportamento correto de mesa: não se abrem duas revisões sobre o mesmo grupo,
reabre-se a que existe.
"""
from __future__ import annotations

import time
import hmac
from datetime import datetime, timezone
from typing import Any

from pymongo import ReadPreference
from pymongo.errors import OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.config import get_settings
from app.db.client import get_client, get_db

FLAG = "under_review"


def open_review(investigation_token: str, reason: str, analyst: str = "demo") -> dict[str, Any]:
    """Revalida composição e valores no snapshot da própria transação."""
    from app.db import ownership
    from app.services import investigation

    proof = investigation.verify(investigation_token)
    client, db, s = get_client(), get_db(), get_settings()
    case_id = investigation.case_id(investigation_token)
    now = datetime.now(timezone.utc)
    started = time.perf_counter()

    def txn(session):
        existing = db.credit_decisions.find_one({"_id": case_id}, session=session)
        if existing:
            if existing["status"] != "open":
                raise investigation.InvestigationError("Esta revisão já foi encerrada. Atualize o grupo.")
            return {"ok": True, "replayed": True, "companies": len(existing["company_ids"]),
                    "companies_blocked": existing["companies_blocked"],
                    "exposures_flagged": existing["exposures_flagged"],
                    "group_exposure": existing["group_exposure"]}

        group = ownership.economic_group(proof["cnpj"], proof["depth"], session=session)
        if not group.get("found") or not group["coverage"]["complete"]:
            raise investigation.InvestigationError("Consulta parcial: amplie a profundidade e atualize o grupo.")
        if not hmac.compare_digest(investigation.fingerprint(group), proof["digest"]):
            raise investigation.InvestigationError("O grupo ou a exposição mudou. Atualize a consulta antes de revisar.")
        company_ids = sorted(n["id"] for n in group["nodes"] if n["kind"] == "company")
        ja_aberto = db.companies.find_one(
            {"_id": {"$in": company_ids}, "credit_status": FLAG, "case_id": {"$ne": None}},
            {"case_id": 1}, session=session,
        )
        if ja_aberto:
            return {"ok": False, "already_open": True, "case_id": ja_aberto["case_id"],
                    "error": "Já existe uma revisão aberta sobre empresas deste grupo."}
        empresas = db.companies.update_many(
            {"_id": {"$in": company_ids}},
            {"$set": {"credit_status": FLAG, "case_id": case_id, "reviewed_at": now}}, session=session,
        )
        if empresas.matched_count != len(company_ids):
            raise investigation.InvestigationError("A composição do grupo mudou. Atualize a consulta.")
        exposicoes = db.credit_exposure.update_many(
            {"company_id": {"$in": company_ids}},
            {"$set": {"review_flag": True, "case_id": case_id}}, session=session,
        )
        evidence = {"cnpj": proof["cnpj"], "depth": proof["depth"], "digest": proof["digest"],
                    "complete": True}
        db.credit_decisions.insert_one({
            "_id": case_id, "company_ids": company_ids, "reason": reason, "analyst": analyst,
            "opened_at": now, "companies_blocked": empresas.modified_count,
            "exposures_flagged": exposicoes.modified_count,
            "group_exposure": group["group_exposure"], "investigation": evidence, "status": "open",
        }, session=session)
        return {"ok": True, "companies": len(company_ids), "companies_blocked": empresas.modified_count,
                "exposures_flagged": exposicoes.modified_count, "group_exposure": group["group_exposure"]}

    with client.start_session() as session:
        result = session.with_transaction(txn, read_concern=ReadConcern("snapshot"),
                                         write_concern=WriteConcern("majority"),
                                         read_preference=ReadPreference.PRIMARY)
    return {"case_id": case_id, **result, "currency": s.currency,
            "read_concern": "snapshot", "write_concern": "majority",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)}


def close_review(case_id: str) -> dict[str, Any]:
    """Libera o grupo. O documento da decisão permanece: auditoria não some."""
    db = get_db()
    client = get_client()
    with client.start_session() as session:

        def txn(s_):
            case = db.credit_decisions.find_one({"_id": case_id}, session=s_)
            if not case:
                return {"ok": False, "error": "caso não encontrado"}
            if case["status"] == "closed":
                return {"ok": True, "case_id": case_id, "replayed": True}
            db.companies.update_many(
                {"case_id": case_id},
                {"$set": {"credit_status": "active", "last_review_case_id": case_id}, "$unset": {"case_id": "", "reviewed_at": ""}},
                session=s_,
            )
            db.credit_exposure.update_many(
                {"case_id": case_id}, {"$unset": {"review_flag": "", "case_id": ""}}, session=s_
            )
            db.credit_decisions.update_one(
                {"_id": case_id}, {"$set": {"status": "closed", "closed_at": datetime.now(timezone.utc)}}, session=s_
            )

            return {"ok": True, "case_id": case_id}

        return session.with_transaction(
            txn,
            read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority"),
            read_preference=ReadPreference.PRIMARY,
        )
    return {"ok": True, "case_id": case_id}


CASE_DETAIL_LIMIT = 200


def case_detail(case_id: str) -> dict[str, Any]:
    db = get_db()
    caso = db.credit_decisions.find_one({"_id": case_id})
    if not caso:
        return {"ok": False, "error": "caso não encontrado"}
    total = db.companies.count_documents({"case_id": case_id})
    empresas = list(
        db.companies.find(
            {"case_id": case_id},
            {"razao_social": 1, "cnpj": 1, "credit_status": 1, "is_holding": 1},
        ).limit(CASE_DETAIL_LIMIT)
    )
    return {
        "ok": True,
        "case": {**caso, "opened_at": caso["opened_at"].isoformat()},
        "companies": [{**c, "status_before": "active"} for c in empresas],
        "stats": {
            "total": total,
            "truncated": total > CASE_DETAIL_LIMIT,
        },
    }


def reset_all() -> dict[str, Any]:
    """Volta a base ao estado pré-demo. Idempotente."""
    db = get_db()
    c = db.companies.update_many(
        {"credit_status": FLAG},
        {"$set": {"credit_status": "active"}, "$unset": {"case_id": "", "reviewed_at": ""}},
    )
    e = db.credit_exposure.update_many(
        {"review_flag": True}, {"$unset": {"review_flag": "", "case_id": ""}}
    )
    db.credit_decisions.delete_many({})
    db.ownership_alerts.delete_many({})
    simuladas = db.ownership.delete_many({"simulated": True})
    return {
        "ok": True,
        "companies_restored": c.modified_count,
        "exposures_restored": e.modified_count,
        "simulated_edges_removed": simuladas.deleted_count,
    }
