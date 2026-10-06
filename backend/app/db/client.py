"""Camada de acesso ao Mongo. Nenhuma rota importa pymongo diretamente.

Duas garantias vivem aqui:

- `serverSelectionTimeoutMS` explícito, para que uma indisponibilidade vire erro
  rápido em vez de uma tela travada no meio da apresentação;
- `with_retry`, que repete apenas falhas transitórias de rede (`AutoReconnect`,
  `NetworkTimeout`) com backoff exponencial. Erro de lógica, de validação ou de
  escrita conflitante **não** é repetido: repetir esconde bug.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from threading import Lock
from typing import Callable, TypeVar

import pymongo
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import AutoReconnect, ConnectionFailure, NetworkTimeout

from app.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T")

TRANSIENT = (AutoReconnect, NetworkTimeout, ConnectionFailure)
MAX_ATTEMPTS = 3
_client_lock = Lock()


@lru_cache(maxsize=1)
def _cached_client() -> MongoClient:
    s = get_settings()
    return MongoClient(
        s.mongodb_uri,
        serverSelectionTimeoutMS=s.server_selection_timeout_ms,
        connectTimeoutMS=s.server_selection_timeout_ms,
        retryWrites=True,
        timeoutMS=s.operation_timeout_ms,
        waitQueueTimeoutMS=5_000,
        appname="atlas-graph-economic-group",
    )


def get_client() -> MongoClient:
    # lru_cache protects its dictionary, but allows concurrent cache misses.
    with _client_lock:
        return _cached_client()


def get_db() -> Database:
    return get_client()[get_settings().db_name]


def bounded_aggregate(coll: Collection, pipeline: list[dict], **kwargs) -> list[dict]:
    """Agregação com o teto `GRAPH_MAX_TIME_MS` efetivamente aplicado.

    Com `timeoutMS` no cliente (CSOT), o driver **ignora** o `maxTimeMS` passado
    por operação e envia o prazo restante do cliente (~25 s). Medido em
    2026-10-06: `aggregate(..., maxTimeMS=15000)` saía com `maxTimeMS: 15590`, e
    com `GRAPH_MAX_TIME_MS=1` o traversal do hub de 5.000 filhas respondia 200.
    `pymongo.timeout()` é a forma suportada de encurtar o prazo de um bloco.
    """
    cap_ms = get_settings().graph_max_time_ms
    with pymongo.timeout(cap_ms / 1000):
        return list(coll.aggregate(pipeline, **kwargs))


def with_retry(fn: Callable[[], T], what: str = "operação") -> T:
    """Backoff exponencial só para falha transitória de rede.

    Prazo estourado (`exc.timeout`) não é transitório: repetir uma agregação que
    já gastou o teto inteiro só triplica a espera do apresentador.
    """
    delay = 0.25
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return fn()
        except TRANSIENT as exc:
            if getattr(exc, "timeout", False) or attempt == MAX_ATTEMPTS:
                log.error("%s falhou após %d tentativas: %s", what, attempt, exc)
                raise
            log.warning("%s: falha transitória (%s), tentativa %d/%d", what, exc, attempt, MAX_ATTEMPTS)
            time.sleep(delay)
            delay *= 2
    raise AssertionError("inalcançável")
