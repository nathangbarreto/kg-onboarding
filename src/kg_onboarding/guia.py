"""Atendimento à solicitação do newcomer: do número da issue ao guia de contribuição.

Etapas da arquitetura (docs/arquitetura.md):
  2.3 Enriquecimento contextual  -> itens semelhantes, pessoas, código citado
  2.4 Consolidação               -> ranqueamento e corte

As etapas 2.2 (interpretação) e 2.5 (síntese) usam o LLM (ver llm.py) e
estão em `gerar_guia`, no fim deste módulo.

Modo replay: com `replay=True`, só se usa o que existia antes da criação da
issue (itens criados antes, eventos anteriores, estado da época). Isso permite
avaliar o contexto comparando com o que de fato aconteceu depois.
"""

from __future__ import annotations

import re
from typing import Any

from kg_onboarding.ferramentas import processo_de_contribuicao
from kg_onboarding.grafo import FILTRO_HUMANO, consultar
from kg_onboarding.llm import gerar, gerar_json

STOPWORDS = set("""a an and are as at be but by can could do does for from has have how i if in
into is it its may me might my no not of on or should so some than that the their them then
there these this to use used using via was we what when where which while who why will with
would you your add allow make support new""".split())

# CamelCase (BloomFilter, ImmutableList) e chamadas qualificadas (Lists.newArrayList)
_CODIGO = re.compile(r"\b(?:[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+|[A-Z]\w*\.[a-z]\w*)\b")

N_SEMELHANTES = 8
N_PESSOAS = 6


def termos_heuristicos(titulo: str, corpo: str | None) -> list[str]:
    """Termos de busca sem LLM: palavras do título (sem stopwords) + código citado."""
    palavras = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9]+", titulo)
                if w.lower() not in STOPWORDS and len(w) > 2]
    return list(dict.fromkeys(palavras + elementos_de_codigo(titulo, corpo)[:5]))


def elementos_de_codigo(titulo: str, corpo: str | None) -> list[str]:
    achados = _CODIGO.findall(f"{titulo}\n{corpo or ''}")
    return [a for a, _ in sorted(((a, achados.count(a)) for a in set(achados)),
                                 key=lambda x: -x[1])][:10]


def _consulta_lucene(termos: list[str]) -> str:
    limpos = [re.sub(r'[+\-&|!(){}\[\]^"~*?:\\/]', " ", t).strip() for t in termos]
    return " ".join(t for t in limpos if t)


def _issue(numero: int) -> dict | None:
    r = consultar("""
        MATCH (i:Issue {number: $numero})
        OPTIONAL MATCH (u:User)-[:OPENED]->(i)
        RETURN i.number AS numero, i.title AS titulo, i.body AS corpo, i.state AS estado,
               [l IN i.labels WHERE NOT l STARTS WITH 'status='] AS labels,
               i.created_at AS criada_em, u.login AS autor
    """, numero=numero)
    return r[0] if r else None


# Fragmento comum: define `ate` (limite temporal) a partir da issue-alvo.
_ALVO = """
    MATCH (alvo:Issue {number: $numero})
    WITH alvo, CASE WHEN $replay THEN alvo.created_at END AS ate
"""


def _semelhantes(numero: int, consulta: str, replay: bool) -> list[dict]:
    if not consulta:
        return []
    return consultar(_ALVO + """
        CALL db.index.fulltext.queryNodes('texto_issues_prs', $consulta) YIELD node AS n, score
        WHERE n.number <> $numero AND (ate IS NULL OR n.created_at < ate)
        WITH n, score, ate ORDER BY score DESC LIMIT $k
        WITH n, score, ate,
             ate IS NULL OR (n.closed_at IS NOT NULL AND n.closed_at < ate) AS encerrado_na_epoca
        RETURN CASE WHEN n:Issue THEN 'issue' ELSE 'pr' END AS tipo, n.number AS numero,
               n.title AS titulo, CASE WHEN encerrado_na_epoca THEN n.state ELSE 'open' END AS estado,
               CASE WHEN encerrado_na_epoca THEN n.state_reason END AS motivo,
               CASE WHEN encerrado_na_epoca THEN
                    n.merged OR exists { (n)<-[:CLOSES]-(:Commit) } END AS aceito,
               n.labels AS labels, round(score, 2) AS relevancia,
               [(c:Commit)-[:CLOSES]->(n) WHERE ate IS NULL OR c.authored_at < ate |
                    {sha: left(c.sha, 10), mensagem: split(c.message, '\n')[0]}][..3] AS commits_que_resolveram,
               [(cc:CommitComment)-[:ON_COMMIT]->(:Commit)-[:CLOSES|MERGED_AS|HAS_COMMIT]-(n)
                    WHERE cc.path IS NOT NULL AND (ate IS NULL OR cc.created_at < ate) | cc.path] AS arquivos
    """, numero=numero, consulta=consulta, replay=replay, k=N_SEMELHANTES)


def _pessoas(numero: int, numeros_semelhantes: list[int], pacotes: list[str],
             autor: str | None, replay: bool) -> list[dict]:
    """Quem participou dos itens semelhantes (peso 2) e de itens do mesmo pacote (peso 1)."""
    return consultar(_ALVO + f"""
        CALL (alvo, ate) {{
            MATCH (n) WHERE (n:Issue OR n:PullRequest) AND n.number IN $semelhantes
            RETURN n, 2 AS peso
          UNION
            MATCH (n) WHERE (n:Issue OR n:PullRequest) AND n <> alvo
              AND any(l IN n.labels WHERE l IN $pacotes)
              AND (ate IS NULL OR n.created_at < ate)
            RETURN n, 1 AS peso
        }}
        CALL (n, ate) {{
            MATCH (u:User)-[:PERFORMED]->(e:TimelineEvent)-[:ON]->(n)
            WHERE e.type IN ['commented', 'closed', 'reviewed', 'merged']
              AND (ate IS NULL OR e.created_at < ate)
            RETURN u, e.type AS papel
          UNION
            MATCH (u:User)-[:AUTHORED]->(c:Commit)-[:CLOSES]->(n)
            WHERE ate IS NULL OR c.authored_at < ate
            RETURN u, 'commit' AS papel
        }}
        WITH u, n, peso, papel
        WHERE {FILTRO_HUMANO} AND u.login <> coalesce($autor, '')
        WITH u, n, max(peso) AS peso, collect(DISTINCT papel) AS papeis
        WITH u, sum(peso) AS pontuacao, count(n) AS itens,
             [x IN collect(n.number) WHERE x IN $semelhantes] AS em_semelhantes,
             reduce(acc = [], ps IN collect(papeis) | acc + [p IN ps WHERE NOT p IN acc]) AS papeis
        RETURN u.login AS login, pontuacao, itens, em_semelhantes, papeis
        ORDER BY pontuacao DESC, itens DESC LIMIT $k
    """, numero=numero, semelhantes=numeros_semelhantes, pacotes=pacotes, autor=autor,
        replay=replay, k=N_PESSOAS)


def mantenedores_ativos(numero: int, autor: str | None, replay: bool) -> list[dict]:
    """Quem mais fechou issues/PRs (até a data da issue, no replay): quem costuma responder."""
    return consultar(_ALVO + f"""
        MATCH (u:User)-[:PERFORMED]->(e:TimelineEvent {{type: 'closed'}})
        WHERE (ate IS NULL OR e.created_at < ate) AND {FILTRO_HUMANO}
          AND u.login <> coalesce($autor, '')
        RETURN u.login AS login, count(*) AS fechamentos ORDER BY fechamentos DESC LIMIT $k
    """, numero=numero, autor=autor, replay=replay, k=N_PESSOAS)


def fundir_pessoas(da_area: list[dict], ativos: list[dict], k: int = N_PESSOAS,
                   rrf_k: int = 60) -> list[dict]:
    """Reciprocal rank fusion: quem atua na área + quem costuma responder.

    No replay (459 issues), a fusão 1:1 superou as duas listas isoladas em hit@3
    (0,47 vs 0,38 da área e 0,44 dos mantenedores). Ver avaliacao/replay.py.
    """
    pontos: dict[str, float] = {}
    motivos: dict[str, list[str]] = {}
    for pos, p in enumerate(da_area):
        pontos[p["login"]] = pontos.get(p["login"], 0) + 1 / (rrf_k + pos)
        motivos.setdefault(p["login"], []).append(
            f"participou de {p['itens']} {'item' if p['itens'] == 1 else 'itens'} da mesma área "
            f"({', '.join(p['papeis'])})"
            + (f"; em semelhantes: {p['em_semelhantes']}" if p["em_semelhantes"] else ""))
    for pos, p in enumerate(ativos):
        pontos[p["login"]] = pontos.get(p["login"], 0) + 1 / (rrf_k + pos)
        motivos.setdefault(p["login"], []).append(
            f"mantenedor ativo ({p['fechamentos']} issues/PRs fechados)")
    ordem = sorted(pontos, key=lambda login: -pontos[login])[:k]
    return [{"login": login, "motivos": motivos[login]} for login in ordem]


def coletar_contexto(numero: int, replay: bool = False,
                     termos: list[str] | None = None) -> dict[str, Any] | None:
    """Contexto consolidado de uma issue, pronto para a síntese pelo LLM."""
    issue = _issue(numero)
    if issue is None:
        return None
    termos = termos or termos_heuristicos(issue["titulo"], issue["corpo"])
    semelhantes = _semelhantes(numero, _consulta_lucene(termos), replay)
    pacotes = [l for l in issue["labels"] if l.startswith("package=")]
    pessoas = fundir_pessoas(
        _pessoas(numero, [s["numero"] for s in semelhantes], pacotes, issue["autor"], replay),
        mantenedores_ativos(numero, issue["autor"], replay),
    )

    arquivos = list(dict.fromkeys(p for s in semelhantes for p in s.pop("arquivos")))
    return {
        "issue": {**issue, "corpo": (issue["corpo"] or "")[:3000]},
        "modo": "replay (só informações anteriores à issue)" if replay else "atual",
        "termos_de_busca": termos,
        "itens_semelhantes": semelhantes,
        "pessoas": pessoas,
        "codigo": {
            "elementos_citados": elementos_de_codigo(issue["titulo"], issue["corpo"]),
            "arquivos_em_revisoes": arquivos[:10],
        },
        # Estatística global (não filtrada no tempo): orientação sobre o processo.
        "processo": processo_de_contribuicao(),
    }


def formatar_markdown(ctx: dict[str, Any]) -> str:
    """Visualização legível do contexto (útil sem LLM e para depuração)."""
    i = ctx["issue"]
    linhas = [f"# Issue #{i['numero']}: {i['titulo']}",
              f"Autor: {i['autor']} · criada em {i['criada_em'][:10]} · labels: {', '.join(i['labels']) or '—'}",
              f"Modo: {ctx['modo']} · termos: {', '.join(ctx['termos_de_busca'])}", "",
              "## Itens semelhantes"]
    for s in ctx["itens_semelhantes"]:
        situacao = s["estado"] + (f"/{s['motivo']}" if s["motivo"] else "") \
            + (" · aceito" if s["aceito"] else "")
        linhas.append(f"- {s['tipo']} #{s['numero']} ({situacao}, rel. {s['relevancia']}): {s['titulo']}")
        for c in s["commits_que_resolveram"]:
            linhas.append(f"    - resolvido por {c['sha']}: {c['mensagem']}")
    linhas += ["", "## Pessoas"]
    for p in ctx["pessoas"]:
        linhas.append(f"- {p['login']}: {'; '.join(p['motivos'])}")
    cod = ctx["codigo"]
    linhas += ["", "## Código", f"- Elementos citados: {', '.join(cod['elementos_citados']) or '—'}",
               f"- Arquivos discutidos em revisões: {', '.join(cod['arquivos_em_revisoes']) or '—'}",
               "", "## Processo (PRs por vínculo do autor)"]
    for r in ctx["processo"]:
        linhas.append(f"- {r['vinculo_autor']}: {r['aceitos']}/{r['prs']} aceitos, "
                      f"mediana {r['dias_medianos_ate_decisao']} dias até a decisão")
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# 2.2 Interpretação e 2.5 Síntese (LLM)
# ---------------------------------------------------------------------------

TIPOS = ["bug", "melhoria", "nova_api", "desempenho", "documentacao", "pergunta", "outro"]

ESQUEMA_INTERPRETACAO = {
    "type": "object",
    "properties": {
        "objetivo": {"type": "string"},
        "tipo": {"type": "string", "enum": TIPOS},
        "termos_busca": {"type": "array", "items": {"type": "string"}},
        "elementos_codigo": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["objetivo", "tipo", "termos_busca", "elementos_codigo"],
}

SISTEMA_INTERPRETACAO = """Você analisa issues do projeto Java open source Guava (bibliotecas do Google).
Dada uma issue, responda em JSON com:
- objetivo: uma frase em português dizendo o que a issue pede ou relata;
- tipo: um de """ + ", ".join(TIPOS) + """;
- termos_busca: de 3 a 8 palavras-chave EM INGLÊS para buscar issues parecidas
  (conceitos técnicos, não palavras genéricas como "issue" ou "problem");
- elementos_codigo: classes ou métodos Java citados ou claramente envolvidos
  (ex.: BloomFilter, Lists.newArrayList); lista vazia se não houver."""

SISTEMA_SINTESE = """Você é um mentor que orienta pessoas que vão contribuir pela primeira vez
com o projeto open source Guava. Escreva em português, de forma clara e concisa.

Regras:
- Use SOMENTE as informações do contexto. Não invente datas, estados, issues,
  pessoas, arquivos nem fatos sobre o processo.
- Cite itens sempre pelo número (ex.: #1067).
- Se uma seção não tiver informação suficiente, diga isso em uma frase.

Responda em Markdown com exatamente estas seis seções, nesta ordem:
## Resumo da issue
(2 ou 3 frases: o que pede, quando foi criada e qual o estado)
## O que já foi discutido
(comente os itens semelhantes mais úteis, um por linha, com número e desfecho)
## Onde investigar no código
(classes e métodos citados no contexto e por onde começar a ler)
## Com quem falar
(as pessoas sugeridas e o motivo de cada uma)
## Como contribuir no Guava
(use os fatos da seção PROCESSO DE CONTRIBUIÇÃO do contexto)
## Próximos passos
(lista numerada com 3 a 5 ações concretas)"""


def interpretar(issue: dict) -> tuple[dict, dict]:
    texto = f"Título: {issue['titulo']}\nLabels: {', '.join(issue['labels']) or '—'}\n\n" \
            f"{(issue['corpo'] or '')[:2500]}"
    return gerar_json(SISTEMA_INTERPRETACAO, texto, ESQUEMA_INTERPRETACAO)


def _contexto_compacto(ctx: dict, interpretacao: dict | None) -> str:
    """Contexto enxuto para a síntese: na CPU, cada mil tokens custam ~10 s."""
    i = ctx["issue"]
    partes = [f"ISSUE #{i['numero']}: {i['titulo']}",
              f"Criada em: {i['criada_em'][:10]} · estado: "
              f"{'aberta (momento da criação)' if ctx['modo'].startswith('replay') else i['estado']}",
              f"Labels: {', '.join(i['labels']) or '—'}"]
    if interpretacao:
        partes.append(f"Interpretação: {interpretacao['objetivo']} (tipo: {interpretacao['tipo']})")
    partes.append(f"Descrição: {(i['corpo'] or '')[:1200]}")
    partes.append("\nITENS SEMELHANTES (mais relevantes primeiro):")
    for s in ctx["itens_semelhantes"][:6]:
        desfecho = s["estado"] + (f"/{s['motivo']}" if s["motivo"] else "") \
            + ("; aceito" if s["aceito"] else "")
        linha = f"- {s['tipo']} #{s['numero']} [{desfecho}]: {s['titulo']}"
        if s["commits_que_resolveram"]:
            linha += " | resolvido por commit: " + s["commits_que_resolveram"][0]["mensagem"]
        partes.append(linha)
    partes.append("\nPESSOAS SUGERIDAS:")
    partes += [f"- {p['login']}: {'; '.join(p['motivos'])}" for p in ctx["pessoas"]]
    cod = ctx["codigo"]
    elementos = list(dict.fromkeys((interpretacao or {}).get("elementos_codigo", [])
                                   + cod["elementos_citados"]))
    partes.append(f"\nCÓDIGO CITADO: {', '.join(elementos) or 'nenhum'}")
    if cod["arquivos_em_revisoes"]:
        partes.append(f"Arquivos discutidos em revisões: {', '.join(cod['arquivos_em_revisoes'])}")
    partes.append("\nPROCESSO DE CONTRIBUIÇÃO (fatos):")
    for r in ctx["processo"]:
        quem = {"NONE": "Quem nunca contribuiu", "CONTRIBUTOR": "Quem já contribuiu antes",
                "MEMBER": "Membros da equipe do Google"}.get(r["vinculo_autor"], r["vinculo_autor"])
        partes.append(f"- {quem}: {r['aceitos']} de {r['prs']} PRs aceitos; decisão em "
                      f"{r['dias_medianos_ate_decisao']} dias (mediana).")
    partes.append("- Um PR aceito pode aparecer como 'closed' sem merge: o Google importa a mudança "
                  "para o repositório interno e um commit sincronizado fecha o PR.")
    partes.append("- Antes de escrever código, comente na issue para combinar a abordagem com os "
                  "mantenedores.")
    return "\n".join(partes)


def gerar_guia(numero: int, replay: bool = False) -> dict[str, Any] | None:
    """Fluxo completo: interpretação -> enriquecimento -> consolidação -> síntese."""
    issue = _issue(numero)
    if issue is None:
        return None
    interpretacao, m_interp = interpretar(issue)
    termos = list(dict.fromkeys(interpretacao["termos_busca"] + interpretacao["elementos_codigo"]))
    ctx = coletar_contexto(numero, replay=replay, termos=termos or None)
    texto, m_sint = gerar(SISTEMA_SINTESE, _contexto_compacto(ctx, interpretacao))
    return {"interpretacao": interpretacao, "contexto": ctx, "guia": texto,
            "metricas": {"interpretacao": m_interp, "sintese": m_sint}}
