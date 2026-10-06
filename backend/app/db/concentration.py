"""Concentração no bloco semelhante à atividade de maior exposição individual.

Busca exata em `activities`; não calcula todos os clusters nem o maior bloco
semântico global. O limiar é calibrado no dado sintético, não um score de risco.
"""
from __future__ import annotations

import time
from typing import Any

from bson.binary import Binary, BinaryVectorDtype

from app.config import get_settings
from app.db.client import bounded_aggregate, get_db, with_retry
from app.db.search import IndexUnavailable, index_status

# Acima disto, duas atividades são o mesmo negócio para efeito de concentração.
# Calibrado no dado desta POV: descrições do mesmo setor ficam acima de 0,80 e
# setores diferentes ficam abaixo. Não é um limiar universal, e a tela mostra o
# score de cada par para o número não virar caixa-preta.
LIMIAR_EQUIVALENCIA = 0.80


def group_concentration(company_ids: list[str]) -> dict[str, Any]:
    s = get_settings()
    db = get_db()
    started = time.perf_counter()

    if not company_ids:
        return {"ok": True, "empty": True, "reason": "nenhum grupo na tela"}

    # --- 1. o que o grupo faz, por exposição ---
    atividades = with_retry(
        lambda: (
            bounded_aggregate(
                db.companies,
                [
                    {"$match": {"_id": {"$in": company_ids}, "is_holding": {"$ne": True}}},
                    {
                        "$lookup": {
                            "from": "credit_exposure",
                            "localField": "_id",
                            "foreignField": "company_id",
                            "as": "cred",
                        }
                    },
                    {
                        "$group": {
                            "_id": "$cnae_descricao",
                            "companies": {"$sum": 1},
                            "limite": {"$sum": {"$ifNull": [{"$first": "$cred.limite"}, 0]}},
                            "vencido": {"$sum": {"$ifNull": [{"$first": "$cred.vencido"}, 0]}},
                            "exemplo": {"$first": "$_id"},
                        }
                    },
                    {"$sort": {"limite": -1}},
                ],
                allowDiskUse=True,
            )
        ),
        "concentration: atividades",
    )
    if not atividades:
        return {"ok": True, "empty": True, "reason": "nenhuma empresa operacional neste grupo"}

    total_limite = sum(a["limite"] for a in atividades)
    lista = [
        {
            "activity": a["_id"],
            "companies": a["companies"],
            "limite": round(a["limite"], 2),
            "vencido": round(a["vencido"], 2),
            "share": round(a["limite"] / total_limite, 4) if total_limite else 0.0,
        }
        for a in atividades
    ]

    # O índice vetorial vive em `activities`, não em `companies`: é lá que
    # está a coisa comparada.
    status = index_status("activities", s.vector_index)
    if status != "READY":
        raise IndexUnavailable(s.vector_index, status)

    # --- 2. a atividade dominante vira a consulta ---
    #
    # A busca roda sobre `activities` — uma linha por descrição distinta —, não
    # sobre `companies`. Comparar significado entre atividades não precisa
    # percorrer 1,2 milhão de empresas que repetem 32 textos: media 29 s daquele
    # jeito e milissegundos deste. O erro é comum e vale citar na demo: indexar a
    # linha em vez de indexar a coisa comparada.
    dominante = atividades[0]
    semente = with_retry(
        lambda: db.activities.find_one(
            {"_id": dominante["_id"], "embedding": {"$exists": True}}, {"embedding": 1}
        ),
        "concentration: vetor semente",
    )
    if not semente:
        raise IndexUnavailable(s.vector_index, "NO_EMBEDDING_FOR_ACTIVITY")

    vetor = semente["embedding"]
    if not isinstance(vetor, Binary):
        vetor = Binary.from_vector(list(vetor), BinaryVectorDtype.FLOAT32)

    # `numCandidates` alto perde o sentido num universo de algumas dezenas de
    # documentos: o teto passa a ser o próprio tamanho da coleção.
    pipeline = [
        {"$vectorSearch": {"index": s.vector_index, "path": "embedding",
                           "queryVector": vetor, "exact": True, "limit": 50}},
        {"$set": {"score": {"$meta": "vectorSearchScore"}}},
        {"$project": {"_id": 1, "score": 1}},
        {"$sort": {"score": -1}},
    ]
    vizinhas = with_retry(lambda: bounded_aggregate(db.activities, pipeline),
        "concentration: atividades equivalentes")
    # Mais de 50 descrições exige aumentar cobertura antes de alegar análise completa.
    if len(vizinhas) == 50:
        raise IndexUnavailable(s.vector_index, "ACTIVITY_CATALOG_LIMIT")
    score_por_atividade = {v["_id"]: v["score"] for v in vizinhas}
    if {a["activity"] for a in lista} - set(score_por_atividade):
        raise IndexUnavailable(s.vector_index, "INCOMPLETE_ACTIVITY_CATALOG")

    # --- 3. exposição semelhante à atividade principal ---
    equivalentes = [
        {"activity": a["activity"], "score": round(score_por_atividade.get(a["activity"], 0), 4), **a}
        for a in lista
        if score_por_atividade.get(a["activity"], 0) >= LIMIAR_EQUIVALENCIA
    ]
    limite_no_bloco = sum(e["limite"] for e in equivalentes)

    return {
        "ok": True,
        "empty": False,
        "currency": s.currency,
        "cnae_count": len(lista),
        "equivalent_activity_count": len(equivalentes),
        "method": "similarity_to_largest_activity",
        "scope_note": "Compara com a atividade de maior exposição individual; não estima todos os negócios nem o maior bloco global.",
        "dominant_activity": dominante["_id"],
        "activities": lista,
        "equivalent_to_dominant": equivalentes,
        "dominant_block_limite": round(limite_no_bloco, 2),
        # A leitura da mesa: quanto do crédito está num negócio só.
        "dominant_block_share": round(limite_no_bloco / total_limite, 4) if total_limite else 0.0,
        "total_limite": round(total_limite, 2),
        "threshold": LIMIAR_EQUIVALENCIA,
        "model": s.embedding_model,
        "dimensions": s.embedding_dimensions,
        "query_details": {
            "operation": "aggregate", "namespace": f"{s.db_name}.activities",
            "pipeline": [{"$vectorSearch": {**pipeline[0]["$vectorSearch"],
                            "queryVector": f"<{s.embedding_dimensions} floats omitidos>"}}, *pipeline[1:]],
            "note": "Busca exata executada; vetor omitido. Tempo inclui a consolidação prévia das atividades.",
        },
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
