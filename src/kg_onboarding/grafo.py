"""Acesso somente leitura ao grafo de conhecimento (catálogo de consultas e fluxo orquestrado)."""

from __future__ import annotations

import os
import re
from functools import cache
from typing import Any

from neo4j import Driver, GraphDatabase, RoutingControl
from neo4j.time import Date, DateTime

from kg_onboarding.config import RAIZ, neo4j_configurado  # noqa: F401  (RAIZ reexportado)

# Contas automatizadas ou de migração que distorcem análises sobre pessoas.
# Além delas, qualquer User com type = 'Bot' é ignorado.
CONTAS_IGNORADAS = ["gissuebot", "googlebot"]

FILTRO_HUMANO = "(u.type <> 'Bot' AND NOT u.login IN $ignorados)"

_ESCRITA = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV|FOREACH)\b|\bCALL\s+(db|dbms|apoc|gds)\.",
    re.IGNORECASE,
)


@cache
def driver() -> Driver:
    return GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )


def disponivel() -> bool:
    """O grafo está configurado e acessível? (decide entre modo ao vivo e offline)."""
    if not neo4j_configurado():
        return False
    try:
        driver().verify_connectivity()
        return True
    except Exception:  # noqa: BLE001
        return False


def _serializavel(valor: Any) -> Any:
    """Converte tipos do Neo4j em tipos JSON (para o LLM e para impressão)."""
    if isinstance(valor, (DateTime, Date)):
        return valor.iso_format()
    if isinstance(valor, list):
        return [_serializavel(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _serializavel(v) for k, v in valor.items()}
    return valor


def consultar(cypher: str, **params: Any) -> list[dict[str, Any]]:
    """Executa uma consulta em modo leitura (o servidor recusa qualquer escrita)."""
    params.setdefault("ignorados", CONTAS_IGNORADAS)
    registros, _, _ = driver().execute_query(
        cypher, parameters_=params, routing_=RoutingControl.READ, database_="neo4j"
    )
    return [_serializavel(r.data()) for r in registros]


def validar_cypher_livre(cypher: str) -> None:
    """Barreira extra para Cypher escrito pelo LLM, além do modo leitura do servidor."""
    sem_textos = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"", "''", cypher)
    if _ESCRITA.search(sem_textos):
        raise ValueError("Somente consultas de leitura são permitidas.")
