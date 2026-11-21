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


# ---------------------------------------------------------------------------
# Guia (recuperação e consolidação do contexto de uma issue)
# ---------------------------------------------------------------------------

from kg_onboarding.guia import coletar_contexto, fundir_pessoas, termos_heuristicos  # noqa: E402


def test_contexto_tem_todas_as_secoes():
    ctx = coletar_contexto(2098)
    assert set(ctx) >= {"issue", "itens_semelhantes", "pessoas", "codigo", "processo"}
    assert ctx["itens_semelhantes"] and ctx["pessoas"]
    assert all(s["numero"] != 2098 for s in ctx["itens_semelhantes"])
    assert not any(l.startswith("status=") for l in ctx["issue"]["labels"])  # não vaza o desfecho


def test_replay_nao_usa_informacao_futura():
    """Análise por snapshot: nada do contexto pode ser posterior à criação da issue."""
    atual = coletar_contexto(2098)
    snapshot = coletar_contexto(2098, replay=True)
    criada = atual["issue"]["criada_em"]
    numeros = [s["numero"] for s in snapshot["itens_semelhantes"]]
    datas = consultar_cypher(
        f"MATCH (n) WHERE (n:Issue OR n:PullRequest) AND n.number IN {numeros} RETURN n.created_at AS d")
    assert all(d["d"] < criada for d in datas)
    # estatísticas do processo de contribuição só com PRs abertos antes da data
    prs_antes = consultar_cypher(
        f"MATCH (:User)-[:OPENED]->(p:PullRequest) WHERE p.created_at < datetime('{criada}') "
        "RETURN count(p) AS n")[0]["n"]
    assert sum(r["prs"] for r in snapshot["processo"]) <= prs_antes
    assert sum(r["prs"] for r in snapshot["processo"]) < sum(r["prs"] for r in atual["processo"])


def test_issue_inexistente():
    assert coletar_contexto(999999) is None


def test_fusao_prioriza_quem_aparece_nas_duas_listas():
    area = [{"login": "a", "itens": 1, "papeis": ["commented"], "em_semelhantes": []},
            {"login": "b", "itens": 1, "papeis": ["commented"], "em_semelhantes": []}]
    ativos = [{"login": "c", "fechamentos": 9}, {"login": "b", "fechamentos": 5}]
    assert fundir_pessoas(area, ativos)[0]["login"] == "b"


def test_termos_heuristicos_tiram_stopwords_e_pegam_codigo():
    termos = termos_heuristicos("Add support for the BloomFilter", "Use Lists.newArrayList here")
    assert "the" not in termos and "BloomFilter" in termos and "Lists.newArrayList" in termos


# ---------------------------------------------------------------------------
# LLM (Claude ou Ollama, ver llm.py): pulados se o provedor não estiver disponível
# ---------------------------------------------------------------------------

from kg_onboarding.guia import ESQUEMA_INTERPRETACAO, TIPOS, _contexto_compacto, interpretar  # noqa: E402


from kg_onboarding import llm  # noqa: E402

requer_llm = pytest.mark.skipif(not llm.disponivel(),
                                reason=f"LLM indisponível ({llm.PROVEDOR}/{llm.MODELO})")


def test_contexto_compacto_cabe_na_janela():
    texto = _contexto_compacto(coletar_contexto(2098), None)
    assert "#2098" in texto and "PROCESSO DE CONTRIBUIÇÃO" in texto
    assert len(texto) < 8000  # ~2 mil tokens: mantém a síntese rápida na CPU


@requer_llm
def test_interpretacao_segue_o_esquema():
    issue = coletar_contexto(2098)["issue"]
    interp, metricas = interpretar(issue)
    assert set(ESQUEMA_INTERPRETACAO["required"]) <= set(interp)
    assert interp["tipo"] in TIPOS and interp["termos_busca"]
    assert metricas["tokens_entrada"] > 0


# ---------------------------------------------------------------------------
# Guias contextuais pré-gerados (modo offline, distribuídos no pacote)
# ---------------------------------------------------------------------------

SECOES_DO_GUIA = ["## Resumo da issue", "## O que já foi discutido", "## Onde investigar no código",
                  "## Com quem falar", "## Como contribuir no Guava", "## Próximos passos"]


def test_guias_pre_gerados_sao_validos():
    from kg_onboarding.config import ARQUIVO_GUIAS
    pacote = json.loads(ARQUIVO_GUIAS.read_text(encoding="utf-8"))
    assert pacote["meta"]["issues"] == len(pacote["guias"]) > 0
    for numero, g in pacote["guias"].items():
        assert g["guia"].strip() and g["contexto"]["issue"]["numero"] == int(numero)
        # as seis seções fixas, todas presentes e na ordem prevista
        posicoes = [g["guia"].find(s) for s in SECOES_DO_GUIA]
        assert -1 not in posicoes and posicoes == sorted(posicoes), f"#{numero}: seções {posicoes}"
    assert "sk-ant" not in ARQUIVO_GUIAS.read_text(encoding="utf-8")  # nada sensível no pacote
