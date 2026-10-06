"""API HTTP. Esta camada expõe HTTP e traduz exceção em código de status.

Nenhuma rota importa `pymongo`: toda query vive em `app/db/`. É o que permite
trocar driver ou versão sem tocar nas rotas.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from bson.errors import BSONError
from pymongo.errors import PyMongoError

from app.config import get_settings
from app.db import concentration, credit_decision, hierarchy, ownership, search
from app.db.client import get_db
from app.services import credit_demo, limits, investigation
from app.services.alerts import TooManySubscribers, hub, sse_stream_async

s = get_settings()
logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("atlas-graph-ownership")


@asynccontextmanager
async def lifespan(_: FastAPI):
    hub.start()
    yield
    hub.stop()


app = FastAPI(title="Atlas Graph — Ownership & Credit Risk", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        f"http://127.0.0.1:{os.getenv('FRONTEND_PORT', 5350)}",
        f"http://localhost:{os.getenv('FRONTEND_PORT', 5350)}",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)



def _mongo_unavailable(exc: Exception) -> dict:
    # A mensagem do driver traz o host do cluster (`<shard>.mongodb.net:27017`).
    # Ela vai para o log do servidor, nunca para o corpo da resposta.
    log.warning("MongoDB indisponível: %s", type(exc).__name__)
    return {
        "feature": "mongodb", "error": "MongoDB indisponível ou prazo da operação excedido.",
        "hint": "Atualize a consulta. Se estava gravando, confira o caso antes de tentar novamente.",
    }


@app.exception_handler(PyMongoError)
async def database_unavailable(request, exc):
    log.warning("MongoDB indisponível em %s: %s", request.url.path, type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": _mongo_unavailable(exc)})

@app.exception_handler(BSONError)
async def request_too_large_for_mongo(request, exc):
    # `DocumentTooLarge` herda de `bson.errors.InvalidDocument`, não de
    # `PyMongoError`: sem este handler, um comando acima de 16 MB vira 500 cru.
    log.warning("comando recusado pelo driver em %s: %s", request.url.path, type(exc).__name__)
    return JSONResponse(status_code=413, content={"detail": {
        "feature": "mongodb", "error": "A consulta montada excede o tamanho aceito pelo MongoDB.",
        "hint": "Reduza a quantidade de ids enviados ou consulte um grupo menor.",
    }})


# --------------------------------------------------------------------------- modelos
# Corpo de POST passa por schema, nunca por checagem manual. Duas falhas reais da
# versão anterior desta POV vinham exatamente daí: `limit: -1` chegando ao motor
# de busca e virando 500, e uma lista de ids recebida como string sendo iterada
# caractere a caractere até a transação abortar.
class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    investigation_token: str = Field(min_length=20, max_length=2048)
    reason: str = Field(default="grupo econômico identificado por traversal", min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Informe o motivo da revisão")
        return value.strip()


# Ids internos têm 44 caracteres (`company_<uuid5>`). Sem teto por item, 2.000
# ids de 10 KB montavam um comando de 20 MB que o driver recusa com
# `DocumentTooLarge` — fora da hierarquia `PyMongoError`, logo um 500 cru.
EntityId = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ConcentrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_ids: list[EntityId] = Field(min_length=1, max_length=2000)


class SearchCompaniesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)
    company_ids: list[EntityId] | None = Field(default=None, max_length=2000)
    node_ids: list[EntityId] | None = Field(default=None, max_length=4000)
    # Escopado ao grafo é o padrão. Abrir para a base inteira é ação deliberada de
    # entity resolution, não o comportamento normal da tela.
    scope_only: bool = True

    @field_validator("q")
    @classmethod
    def q_nao_so_espaco(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("`q` cannot be whitespace only")
        return v


# --------------------------------------------------------------------------- saúde
@app.get("/health/live")
def live() -> dict:
    """Liveness puro: o processo está de pé. Não toca no banco."""
    return {"status": "alive"}


@app.get("/health")
def health() -> dict:
    """Checagem profunda, e é o checklist pré-demo.

    Valida de uma vez: conexão, contagens das coleções, estado do índice de busca
    e a latência de uma consulta pontual por CNPJ — que é o caminho principal da
    demonstração e o primeiro a degradar se algo estiver errado.
    """
    checks: dict = {}
    status = "ok"
    db = get_db()

    try:
        t0 = time.perf_counter()
        db.command("ping")
        checks["mongodb"] = {"ok": True, "ping_ms": round((time.perf_counter() - t0) * 1000, 1)}
    except PyMongoError as exc:
        return {"status": "down", "checks": {"mongodb": {"ok": False, "error": type(exc).__name__}}}

    try:
        checks["counts"] = {
            "companies": db.companies.estimated_document_count(),
            "ownership": db.ownership.estimated_document_count(),
            "credit_exposure": db.credit_exposure.estimated_document_count(),
            "people": db.people.estimated_document_count(),
        }
        if checks["counts"]["companies"] == 0:
            status = "degraded"
    except PyMongoError as exc:
        status = "degraded"
        checks["counts"] = {"error": type(exc).__name__}

    try:
        checks["search_index"] = {
            s.company_search_index: search.index_status("companies", s.company_search_index),
            s.people_search_index: search.index_status("people", s.people_search_index),
            s.vector_index: search.index_status("activities", s.vector_index),
        }
        if any(v != "READY" for v in checks["search_index"].values()):
            status = "degraded"
    except Exception as exc:  # noqa: BLE001 — o health nunca pode derrubar a si mesmo
        checks["search_index"] = {"error": type(exc).__name__}
        status = "degraded"

    try:
        alvo = db.companies.find_one({}, {"cnpj": 1})
        t0 = time.perf_counter()
        if alvo:
            probe = ownership.economic_group(alvo["cnpj"], depth=2)
        else:
            probe = {"found": False}
        if not probe.get("found"):
            status = "degraded"
        checks["graphlookup_probe"] = {
            "ok": bool(probe.get("found")),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
    except PyMongoError as exc:
        status = "degraded"
        checks["graphlookup_probe"] = {"ok": False, "error": type(exc).__name__}

    checks["bulkhead"] = limits.estado()
    checks["change_stream"] = hub.state
    if not hub.state.get("running"):
        status = "degraded"
    return {"status": status, "checks": checks}


# --------------------------------------------------------------------------- grafo
@app.get("/api/entry-points")
def entry_points():
    """Casos preparados: solicitantes de crédito e o controle sem grupo."""
    return credit_demo.entry_points()


@app.get("/api/group/{cnpj}")
def economic_group(cnpj: str, depth: int | None = Query(None, description=f"limitado a {s.depth_cap}")):
    """Cadeia societária e exposição consolidada — o caminho principal da demo."""
    try:
        result = ownership.economic_group(cnpj.strip(), depth=depth)
    except PyMongoError as exc:
        raise HTTPException(503, detail=_mongo_unavailable(exc)) from exc
    if result.get("too_large"):
        raise HTTPException(
            503,
            detail={
                "feature": "graph_traversal",
                "error": result["error"],
                "hint": result["hint"],
                "depth": result["depth"],
            },
        )
    if not result.get("found"):
        raise HTTPException(404, f"CNPJ `{cnpj}` não encontrado")
    result["investigation"] = investigation.issue(result)
    return result


# ------------------------------------------------------------------ hierarquia
@app.get("/api/hierarchy/{advisor_id}/portfolio")
def advisor_portfolio(advisor_id: str, limit: int = Query(50, ge=1, le=200)):
    """Carteira consolidada do escopo do usuário.

    O escopo **não** vem do cliente: o backend desce a hierarquia a partir de quem
    o usuário é. Um gerente alcança a carteira de todos os assessores abaixo dele;
    um assessor alcança só a própria.
    """
    try:
        # Bulkhead: a carteira de um escopo grande soma dezenas de milhares de
        # exposições. Sem vaga, recusa em 750 ms em vez de entrar numa fila que
        # atrasa também a consulta interativa. Ver `app/services/limits.py`.
        with limits.vaga("carteira"):
            result = hierarchy.portfolio(advisor_id, limite_empresas=limit)
    except PyMongoError as exc:
        raise HTTPException(503, detail=_mongo_unavailable(exc)) from exc
    if not result.get("found"):
        raise HTTPException(404, f"usuário `{advisor_id}` não encontrado")
    return result


@app.get("/api/hierarchy/{advisor_id}/can-see/{cnpj}")
def advisor_can_see(advisor_id: str, cnpj: str):
    """Este usuário pode ver esta conta? Resposta derivada da árvore, não de lista."""
    try:
        result = hierarchy.can_see(advisor_id, cnpj.strip())
    except PyMongoError as exc:
        raise HTTPException(503, detail=_mongo_unavailable(exc)) from exc
    if not result.get("found"):
        raise HTTPException(404, f"CNPJ `{cnpj}` não encontrado")
    return result


@app.get("/api/hierarchy/roster")
def hierarchy_roster():
    """Usuários de exemplo para a demo.

    Não é uma amostra qualquer da tabela: são os usuários que **cobrem o grupo de
    vitrine** — os assessores que atendem as empresas dele, o gerente de um deles
    e o regional acima. Escolher pelo primeiro registro de cada papel devolvia
    gente de outro ramo da árvore, e aí todos os perfis enxergavam a mesma fatia
    do grupo: a hierarquia não significava nada na tela.
    """
    db = get_db()
    grupo = db.economic_groups.find_one({"showcase": True}, sort=[("sector", 1)])
    ids_assessores: list[str] = []
    if grupo:
        vistos: set[str] = set()
        for c in db.companies.find(
            {"_id": {"$in": grupo["member_ids"]}}, {"advisor_id": 1}
        ):
            a = c.get("advisor_id")
            if a and a not in vistos:
                vistos.add(a)
                ids_assessores.append(a)

    campos = {"nome": 1, "papel": 1, "matricula": 1, "regiao": 1, "segmento": 1}
    assessores = list(db.advisors.find({"_id": {"$in": ids_assessores[:3]}}, campos))

    # Sobe pelo gerente que cobre **mais** do grupo, não pelo primeiro assessor
    # da lista. Subir pelo primeiro devolvia um gerente que cobre um assessor só,
    # e a tela mostrava gerente e assessor enxergando exatamente a mesma fatia —
    # o degrau da hierarquia sumia justamente no passo que a demo quer mostrar.
    acima: list[dict] = []
    if assessores:
        por_gerente: dict[str, int] = {}
        for a in db.advisors.find(
            {"_id": {"$in": [a["_id"] for a in assessores]}}, {"reports_to": 1}
        ):
            if a.get("reports_to"):
                por_gerente[a["reports_to"]] = por_gerente.get(a["reports_to"], 0) + 1
        if por_gerente:
            melhor = max(por_gerente, key=por_gerente.get)
            atual = melhor
            while atual and len(acima) < 2:
                sup = db.advisors.find_one({"_id": atual}, campos)
                if sup and sup.get("papel") != "superintendente":
                    acima.append(sup)
                doc = db.advisors.find_one({"_id": atual}, {"reports_to": 1})
                atual = (doc or {}).get("reports_to")

    # Do topo para a base: é a ordem em que o roteiro percorre a tela.
    return {"users": list(reversed(acima)) + assessores}


# --------------------------------------------------------------------------- busca
@app.post("/api/search/companies")
def search_companies(payload: SearchCompaniesIn):
    """Atlas Search sobre razão social, ciente do grupo que está na tela."""
    try:
        return search.resolve_company(
            payload.q.strip(),
            limit=payload.limit,
            company_ids=payload.company_ids,
            node_ids=payload.node_ids,
            escopo_apenas=payload.scope_only,
        )
    except search.IndexUnavailable as exc:
        raise HTTPException(
            503, detail={"feature": "atlas_search", "index": exc.index_name, "status": exc.status}
        ) from exc


@app.post("/api/analysis/concentration")
def group_concentration(payload: ConcentrationIn):
    """Leitura semântica automática: o grupo é tão diversificado quanto parece?

    Sem consulta digitada — a pergunta é feita pelo próprio grafo.
    """
    try:
        with limits.vaga("concentracao"):
            return concentration.group_concentration(payload.company_ids)
    except search.IndexUnavailable as exc:
        raise HTTPException(
            503, detail={"feature": "vector_search", "index": exc.index_name, "status": exc.status}
        ) from exc


# --------------------------------------------------------------------------- decisão
@app.post("/api/credit/review")
def open_review(payload: ReviewIn):
    """Transação ACID sobre o grupo econômico inteiro."""
    try:
        result = credit_decision.open_review(payload.investigation_token, reason=payload.reason)
    except investigation.InvestigationError as exc:
        raise HTTPException(409, detail={"error": str(exc), "refresh_required": True}) from exc
    if not result.get("ok"):
        raise HTTPException(
            409,
            detail={
                "error": result.get("error", "transaction aborted"),
                "case_id": result.get("case_id"),
                "already_open": result.get("already_open", False),
            },
        )
    return result


@app.get("/api/credit/case/{case_id}")
def case(case_id: str):
    result = credit_decision.case_detail(case_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error"))
    return result


@app.post("/api/credit/close/{case_id}")
def close(case_id: str):
    result = credit_decision.close_review(case_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error"))
    return result


# --------------------------------------------------------------------------- demo
@app.post("/api/demo/reset")
def reset():
    out = credit_decision.reset_all()
    credit_demo.invalidate_cache()
    return out


# --------------------------------------------------------------------------- tempo real
@app.get("/api/alerts/stream")
async def alerts_stream(request: Request):
    # Assíncrono de propósito: um gerador síncrono prendia uma thread do pool por
    # aba aberta e, com ~40 abas, travava todas as rotas síncronas da API.
    try:
        q = hub.subscribe()
    except TooManySubscribers as exc:
        raise HTTPException(503, detail={
            "feature": "alerts_stream", "error": str(exc),
            "hint": "Feche abas antigas da demo; os alertas recentes seguem em /api/alerts/recent.",
        }) from exc

    async def gen():
        try:
            async for chunk in sse_stream_async(q, request.is_disconnected):
                yield chunk
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/alerts/recent")
def alerts_recent(limit: int = Query(20, ge=1, le=100)):
    docs = list(get_db().ownership_alerts.find().sort("created_at", -1).limit(limit))
    return {"alerts": docs, "listener": hub.state}
