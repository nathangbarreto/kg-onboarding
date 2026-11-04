"""Testes de integração das ferramentas (exigem o Neo4j rodando com o grafo carregado).

uv run pytest
"""

import json
from pathlib import Path

import pytest

from kg_onboarding.ferramentas import FERRAMENTAS, consultar_cypher
from kg_onboarding.grafo import driver

GABARITO = Path(__file__).resolve().parents[1] / "avaliacao" / "perguntas.jsonl"
PERGUNTAS = [json.loads(linha) for linha in GABARITO.read_text().splitlines() if linha.strip()]


@pytest.fixture(scope="session", autouse=True)
def neo4j_disponivel():
    try:
        driver().verify_connectivity()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j indisponível: {e}")


def test_gabarito_usa_ferramentas_existentes():
    for p in PERGUNTAS:
        assert p["ferramenta"] in FERRAMENTAS, p["id"]


@pytest.mark.parametrize("p", [p for p in PERGUNTAS if p["argumentos"] is not None],
                         ids=lambda p: p["id"])
def test_chamada_esperada_retorna_dados(p):
    resultado = FERRAMENTAS[p["ferramenta"]](**p["argumentos"])
    assert resultado, f"{p['id']}: {p['ferramenta']}({p['argumentos']}) não retornou nada"


def test_esquemas_sao_validos_para_tool_use():
    for f in FERRAMENTAS.values():
        e = f.esquema()
        assert e["name"] and e["description"]
        assert e["input_schema"]["type"] == "object"
        assert set(e["input_schema"]["required"]) <= set(e["input_schema"]["properties"])


@pytest.mark.parametrize("cypher", [
    "MATCH (n) DETACH DELETE n",
    "CREATE (:X)",
    "MATCH (u:User) SET u.login = 'x'",
    "MERGE (:X {id: 1})",
    "CALL dbms.components()",
])
def test_cypher_livre_bloqueia_escrita(cypher):
    with pytest.raises(ValueError):
        consultar_cypher(cypher)


def test_cypher_livre_permite_leitura():
    assert consultar_cypher("MATCH (r:Release) RETURN r.name LIMIT 1")
    # palavras reservadas dentro de strings não devem bloquear a consulta
    assert consultar_cypher("MATCH (i:Issue) WHERE i.title CONTAINS 'Set' RETURN i.number LIMIT 1")
