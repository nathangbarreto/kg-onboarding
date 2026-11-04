"""Utilitários compartilhados pelos scripts de ETL (SQLite -> Neo4j)."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable, Iterator
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase

RAIZ = Path(__file__).resolve().parents[2]
SQLITE_PATH = RAIZ / "data" / "raw" / "guava.sqlite"
TAMANHO_LOTE = 1000


def conectar_sqlite() -> sqlite3.Connection:
    """Abre o banco de origem em modo somente leitura."""
    conn = sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def conectar_neo4j() -> Driver:
    load_dotenv(RAIZ / ".env")
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    driver.verify_connectivity()
    return driver


def js(valor: str | None) -> Any:
    """Decodifica uma coluna JSON do SQLite (None continua None)."""
    return json.loads(valor) if valor else None


def dt(valor: str | None) -> datetime | None:
    """Converte '2014-10-31T17:03:58.000Z' em datetime (vira DateTime no Neo4j)."""
    return datetime.fromisoformat(valor) if valor else None


def booleano(valor: int | None) -> bool | None:
    return None if valor is None else bool(valor)


def lotes(itens: Iterable[dict], tamanho: int = TAMANHO_LOTE) -> Iterator[list[dict]]:
    it = iter(itens)
    while lote := list(islice(it, tamanho)):
        yield lote


def executar_em_lotes(driver: Driver, cypher: str, linhas: Iterable[dict]) -> int:
    """Executa `cypher` (que deve começar com UNWIND $linhas AS l) em lotes.

    Retorna quantas linhas foram enviadas.
    """
    total = 0
    for lote in lotes(linhas):
        driver.execute_query(cypher, linhas=lote, database_="neo4j")
        total += len(lote)
    return total
