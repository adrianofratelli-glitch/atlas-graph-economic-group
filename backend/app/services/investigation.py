"""Comprovante assinado da consulta: integridade e validade, não autenticação.

A chave efêmera invalida comprovantes após restart. Em múltiplos workers, configure
INVESTIGATION_SIGNING_KEY igual para todos. Nenhuma coleção ou índice adicional.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from app.config import get_settings

_KEY = (get_settings().investigation_signing_key or secrets.token_hex(32)).encode()
TTL_SECONDS = 900


class InvestigationError(ValueError):
    pass


def fingerprint(group: dict) -> str:
    # Estado de revisão não participa: reenvio do mesmo comprovante é idempotente.
    facts = {
        "cnpj": group["subject"]["cnpj"], "depth": group["depth"],
        "nodes": sorted((n["id"], n.get("limite", 0), n.get("utilizado", 0), n.get("vencido", 0))
                        for n in group["nodes"]),
        "edges": sorted((e["from"], e["to"], e["type"], e["percentage"]) for e in group["edges"]),
        "exposure": group["group_exposure"], "coverage": group["coverage"],
    }
    return hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def issue(group: dict) -> dict:
    now = int(time.time())
    payload = {"cnpj": group["subject"]["cnpj"], "depth": group["depth"],
               "digest": fingerprint(group), "issued": now, "expires": now + TTL_SECONDS,
               "nonce": secrets.token_hex(12)}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    token = raw + "." + hmac.new(_KEY, raw.encode(), hashlib.sha256).hexdigest()
    return {"token": token, "expires_at": payload["expires"],
            "review_allowed": group["coverage"]["complete"]}


def verify(token: str) -> dict:
    try:
        raw, signature = token.split(".")
        expected = hmac.new(_KEY, raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(raw))
        if not (payload["issued"] <= time.time() <= payload["expires"]):
            raise ValueError()
        return payload
    except (ValueError, KeyError, TypeError) as exc:
        raise InvestigationError("Consulta inválida ou expirada. Atualize o grupo antes de abrir a revisão.") from exc


def case_id(token: str) -> str:
    return "credit_" + hashlib.sha256(token.encode()).hexdigest()[:24]
