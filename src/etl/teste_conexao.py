"""Verifica o ambiente: .env, banco SQLite do guava e conexão com o Neo4j."""

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

RAIZ = Path(__file__).resolve().parents[2]
SQLITE_PATH = RAIZ / "data" / "raw" / "guava.sqlite"
ISSUES_ESPERADAS = 1446


def verificar_env() -> bool:
    carregou = load_dotenv(RAIZ / ".env")
    faltando = [v for v in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD") if not os.getenv(v)]
    ok = carregou and not faltando
    print(f"[{'OK' if ok else 'FALHA'}] .env carregado" + (f" (faltando: {faltando})" if faltando else ""))
    return ok


def verificar_sqlite() -> bool:
    try:
        with sqlite3.connect(f"file:{SQLITE_PATH}?mode=ro", uri=True) as conn:
            total = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    except sqlite3.Error as e:
        print(f"[FALHA] SQLite: {e}")
        return False
    ok = total == ISSUES_ESPERADAS
    print(f"[{'OK' if ok else 'FALHA'}] SQLite: {total} linhas em issues (esperado {ISSUES_ESPERADAS})")
    return ok


def verificar_neo4j() -> bool:
    uri = os.environ["NEO4J_URI"]
    auth = (os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])
    try:
        with GraphDatabase.driver(uri, auth=auth) as driver:
            driver.verify_connectivity()
    except Exception as e:
        print(f"[FALHA] Neo4j ({uri}): {e}")
        return False
    print(f"[OK] Neo4j: conectado em {uri}")
    return True


def main() -> None:
    resultados = [verificar_env(), verificar_sqlite()]
    resultados.append(verificar_neo4j() if resultados[0] else False)
    print("Tudo OK." if all(resultados) else "Há verificações com falha.")
    raise SystemExit(0 if all(resultados) else 1)


if __name__ == "__main__":
    main()
