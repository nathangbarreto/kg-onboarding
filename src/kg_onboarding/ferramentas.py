"""Catálogo de consultas ao grafo de conhecimento.

Nove consultas curadas, cada uma ligada a uma pergunta típica de newcomer (ver
docs/perguntas_newcomer.md), e uma consulta livre em Cypher restrita a leitura.
O registro FERRAMENTAS guarda nome, descrição e parâmetros em JSON Schema, no
formato aceito pelas APIs de LLM com chamada de ferramentas.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kg_onboarding.grafo import FILTRO_HUMANO, consultar, validar_cypher_livre


@dataclass(frozen=True)
class Ferramenta:
    nome: str
    descricao: str
    parametros: dict[str, Any]
    funcao: Callable[..., Any]

    def esquema(self) -> dict[str, Any]:
        """Definição da ferramenta no formato de tool use (name/description/input_schema)."""
        return {"name": self.nome, "description": self.descricao, "input_schema": self.parametros}

    def __call__(self, **kwargs: Any) -> Any:
        return self.funcao(**kwargs)


FERRAMENTAS: dict[str, Ferramenta] = {}


def ferramenta(descricao: str, propriedades: dict[str, Any] | None = None,
               obrigatorios: list[str] | None = None):
    def registrar(func: Callable[..., Any]) -> Callable[..., Any]:
        FERRAMENTAS[func.__name__] = Ferramenta(
            nome=func.__name__,
            descricao=descricao,
            parametros={"type": "object", "properties": propriedades or {},
                        "required": obrigatorios or []},
            funcao=func,
        )
        return func
    return registrar


def _label_pacote(pacote: str) -> str:
    return pacote if pacote.startswith("package=") else f"package={pacote}"


LIMITE = {"type": "integer", "description": "Máximo de itens retornados.", "minimum": 1, "maximum": 50}


# ---------------------------------------------------------------------------
# Encontrar onde contribuir
# ---------------------------------------------------------------------------

@ferramenta(
    "Lista os pacotes do projeto (ex.: collect, base, cache), derivados das labels "
    "'package=*', com quantidade de issues e PRs e a data da última atividade. "
    "Use para o newcomer escolher uma área.",
)
def listar_pacotes() -> list[dict]:
    return consultar("""
        MATCH (n) WHERE n:Issue OR n:PullRequest
        UNWIND n.labels AS label
        WITH n, label WHERE label STARTS WITH 'package='
        RETURN substring(label, 8) AS pacote,
               count(CASE WHEN n:Issue THEN 1 END) AS issues,
               count(CASE WHEN n:PullRequest THEN 1 END) AS prs,
               max(n.updated_at) AS ultima_atividade
        ORDER BY issues + prs DESC
    """)


@ferramenta(
    "Busca issues e PRs por palavras no título e no corpo (índice de texto completo). "
    "Use para ver se um problema ou ideia já foi discutido antes de abrir algo novo.",
    {
        "texto": {"type": "string", "description": "Palavras-chave, em inglês (idioma do projeto)."},
        "tipo": {"type": "string", "enum": ["todos", "issue", "pr"], "description": "Filtra por tipo."},
        "limite": LIMITE,
    },
    ["texto"],
)
def buscar_discussoes(texto: str, tipo: str = "todos", limite: int = 10) -> list[dict]:
    termo = re.sub(r'[+\-&|!(){}\[\]^"~*?:\\/]', " ", texto).strip()  # escapa sintaxe Lucene
    if not termo:
        return []
    return consultar("""
        CALL db.index.fulltext.queryNodes('texto_issues_prs', $termo) YIELD node AS n, score
        WITH n, score, CASE WHEN n:Issue THEN 'issue' ELSE 'pr' END AS tipo
        WHERE $tipo = 'todos' OR tipo = $tipo
        RETURN tipo, n.number AS numero, n.title AS titulo, n.state AS estado,
               n.labels AS labels, round(score, 2) AS relevancia
        ORDER BY score DESC LIMIT $limite
    """, termo=termo, tipo=tipo, limite=limite)


@ferramenta(
    "Lista PRs abertos (trabalho em andamento), opcionalmente de um pacote. "
    "Use para evitar trabalho duplicado ou achar PRs que precisam de ajuda.",
    {"pacote": {"type": "string", "description": "Nome do pacote, ex.: 'collect'."}, "limite": LIMITE},
)
def prs_abertos(pacote: str | None = None, limite: int = 20) -> list[dict]:
    return consultar("""
        MATCH (u:User)-[:OPENED]->(p:PullRequest {state: 'open'})
        WHERE $label IS NULL OR $label IN p.labels
        RETURN p.number AS numero, p.title AS titulo, u.login AS autor,
               p.created_at AS criado_em, p.labels AS labels, p.comments AS comentarios
        ORDER BY p.created_at DESC LIMIT $limite
    """, label=_label_pacote(pacote) if pacote else None, limite=limite)


# ---------------------------------------------------------------------------
# Encontrar pessoas
# ---------------------------------------------------------------------------

@ferramenta(
    "Pessoas mais ativas em um pacote: quem comenta, revisa, abre PRs e escreve "
    "commits que fecham issues/PRs com aquela label. Use para indicar com quem "
    "conversar ou quem pode revisar. Exclui bots.",
    {"pacote": {"type": "string", "description": "Nome do pacote, ex.: 'cache'."}, "limite": LIMITE},
    ["pacote"],
)
def especialistas_do_pacote(pacote: str, limite: int = 10) -> list[dict]:
    return consultar(f"""
        MATCH (n) WHERE (n:Issue OR n:PullRequest) AND $label IN n.labels
        CALL (n) {{
            MATCH (u:User)-[:PERFORMED]->(:TimelineEvent {{type: 'commented'}})-[:ON]->(n)
            RETURN u, 'comentario' AS tipo
          UNION ALL
            MATCH (u:User)-[:REVIEWED]->(n) RETURN u, 'revisao' AS tipo
          UNION ALL
            MATCH (u:User)-[:OPENED]->(n:PullRequest) RETURN u, 'pr' AS tipo
          UNION ALL
            MATCH (u:User)-[:AUTHORED]->(:Commit)-[:CLOSES]->(n) RETURN u, 'commit' AS tipo
          UNION ALL
            MATCH (u:User)-[:MERGED]->(n) RETURN u, 'merge' AS tipo
        }}
        WITH u, n, tipo WHERE {FILTRO_HUMANO}
        WITH u,
             count(DISTINCT CASE WHEN tipo = 'comentario' THEN n END) AS comentarios,
             count(DISTINCT CASE WHEN tipo = 'revisao' THEN n END) AS revisoes,
             count(DISTINCT CASE WHEN tipo = 'pr' THEN n END) AS prs,
             count(DISTINCT CASE WHEN tipo = 'commit' THEN n END) AS commits,
             count(DISTINCT CASE WHEN tipo = 'merge' THEN n END) AS merges
        RETURN u.login AS login, u.name AS nome, comentarios, revisoes, prs, commits, merges,
               comentarios + 2 * prs + 3 * (revisoes + commits + merges) AS pontuacao
        ORDER BY pontuacao DESC LIMIT $limite
    """, label=_label_pacote(pacote), limite=limite)


@ferramenta(
    "Mantenedores: quem faz merge e revisa PRs e quem fecha issues no projeto todo. "
    "Use para explicar quem decide o que entra. Exclui bots.",
    {"limite": LIMITE},
)
def mantenedores(limite: int = 10) -> list[dict]:
    return consultar(f"""
        MATCH (u:User) WHERE {FILTRO_HUMANO}
        WITH u,
             COUNT {{ (u)-[:MERGED]->(:PullRequest) }} AS merges,
             COUNT {{ (u)-[:REVIEWED]->(:PullRequest) }} AS revisoes,
             COUNT {{ (u)-[:PERFORMED]->(:TimelineEvent {{type: 'closed'}}) }} AS fechamentos
        WHERE merges + revisoes + fechamentos > 0
        RETURN u.login AS login, u.name AS nome, merges, revisoes, fechamentos
        ORDER BY merges + revisoes + fechamentos DESC LIMIT $limite
    """, limite=limite)


@ferramenta(
    "Perfil de um contribuidor pelo login do GitHub: o que já fez no projeto "
    "(issues, PRs, commits, revisões, comentários) e em quais pacotes atua.",
    {"login": {"type": "string", "description": "Login no GitHub, ex.: 'cpovirk'."}},
    ["login"],
)
def perfil_contribuidor(login: str) -> dict | None:
    r = consultar("""
        MATCH (u:User {login: $login})
        CALL (u) {
            CALL (u) {
                MATCH (u)-[:OPENED|REVIEWED|MERGED]->(x) RETURN x
              UNION
                MATCH (u)-[:PERFORMED]->(:TimelineEvent)-[:ON]->(x) RETURN x
              UNION
                MATCH (u)-[:AUTHORED]->(:Commit)-[:CLOSES]->(x) RETURN x
            }
            UNWIND x.labels AS label
            WITH label WHERE label STARTS WITH 'package='
            WITH substring(label, 8) AS pacote, count(*) AS itens ORDER BY itens DESC
            RETURN collect({pacote: pacote, itens: itens})[..5] AS pacotes
        }
        RETURN u.login AS login, u.name AS nome, u.company AS empresa, u.type AS tipo,
               u.contributions AS contribuicoes_github,
               COUNT { (u)-[:OPENED]->(:Issue) } AS issues_abertas,
               COUNT { (u)-[:OPENED]->(:PullRequest) } AS prs_abertos,
               COUNT { (u)-[:AUTHORED]->(:Commit) } AS commits,
               COUNT { (u)-[:REVIEWED]->(:PullRequest) } AS revisoes,
               COUNT { (u)-[:MERGED]->(:PullRequest) } AS merges,
               COUNT { (u)-[:PERFORMED]->(:TimelineEvent {type: 'commented'}) } AS comentarios,
               pacotes AS pacotes_principais
    """, login=login)
    return r[0] if r else None


# ---------------------------------------------------------------------------
# Entender uma issue ou PR
# ---------------------------------------------------------------------------

@ferramenta(
    "Detalhes de uma issue ou PR pelo número: título, estado, labels, autor, "
    "responsáveis, milestone, commits que a fecharam ou referenciaram, commits do PR "
    "e itens relacionados (menções cruzadas).",
    {"numero": {"type": "integer", "description": "Número da issue ou PR."}},
    ["numero"],
)
def detalhes(numero: int) -> dict | None:
    r = consultar("""
        MATCH (n {number: $numero}) WHERE n:Issue OR n:PullRequest
        OPTIONAL MATCH (autor:User)-[:OPENED]->(n)
        OPTIONAL MATCH (n)-[:IN_MILESTONE]->(m:Milestone)
        RETURN CASE WHEN n:Issue THEN 'issue' ELSE 'pr' END AS tipo,
               n.number AS numero, n.title AS titulo, n.state AS estado,
               n.state_reason AS motivo_estado, n.merged AS merged, n.labels AS labels,
               autor.login AS autor, n.author_association AS vinculo_autor,
               n.created_at AS criado_em, n.closed_at AS fechado_em, m.title AS milestone,
               left(n.body, 1500) AS corpo,
               [(r:User)-[:ASSIGNED_TO]->(n) | r.login] AS responsaveis,
               [(c:Commit)-[rel:CLOSES|REFERENCES]->(n) |
                   {sha: left(c.sha, 10), relacao: type(rel),
                    mensagem: split(c.message, '\n')[0],
                    autor: head([(a:User)-[:AUTHORED]->(c) | a.login])}] AS commits_ligados,
               [(n)-[:HAS_COMMIT]->(c:Commit) | {sha: left(c.sha, 10),
                    mensagem: split(c.message, '\n')[0]}][..20] AS commits_do_pr,
               [(n)-[:MERGED_AS]->(c:Commit) | left(c.sha, 10)][0] AS commit_de_merge,
               [(u:User)-[:MERGED]->(n) | u.login][0] AS merge_feito_por,
               [(u:User)-[rv:REVIEWED]->(n) | {revisor: u.login, estado: rv.state}] AS revisoes,
               [(n)-[:MENTIONS]->(x) | {numero: x.number, titulo: x.title}] AS menciona,
               [(x)-[:MENTIONS]->(n) | {numero: x.number, titulo: x.title}] AS mencionado_por
    """, numero=numero)
    return r[0] if r else None


@ferramenta(
    "Linha do tempo de uma issue ou PR: eventos em ordem (comentários com texto, "
    "labels, atribuições, commits, revisões, fechamento). Use para resumir a discussão.",
    {"numero": {"type": "integer", "description": "Número da issue ou PR."},
     "limite": {**LIMITE, "maximum": 200}},
    ["numero"],
)
def linha_do_tempo(numero: int, limite: int = 50) -> list[dict]:
    return consultar("""
        MATCH (n {number: $numero})<-[:ON]-(e:TimelineEvent) WHERE n:Issue OR n:PullRequest
        OPTIONAL MATCH (u:User)-[:PERFORMED]->(e)
        RETURN e.position AS posicao, e.type AS evento, u.login AS quem, e.created_at AS quando,
               e.label_name AS label, e.review_state AS revisao,
               left(e.commit_id, 10) AS commit, left(e.body, 600) AS texto
        ORDER BY e.position LIMIT $limite
    """, numero=numero, limite=limite)


# ---------------------------------------------------------------------------
# Entender o processo
# ---------------------------------------------------------------------------

@ferramenta(
    "Estatísticas do processo de contribuição: PRs por vínculo do autor (MEMBER, "
    "CONTRIBUTOR, NONE = primeira vez), quantos foram aceitos e o tempo mediano até "
    "a decisão. No guava, muitos PRs são aceitos sem merge no GitHub: a mudança é "
    "importada internamente pelo Google e um commit fecha o PR. Aqui 'aceito' = "
    "merged OU fechado por commit. PRs abertos por bots (ex.: sincronização automática "
    "do copybara) são excluídos.",
)
def processo_de_contribuicao(ate: str | None = None) -> list[dict]:
    """Sem `ate`: estado atual. Com `ate` (data ISO, usada na análise por snapshot):
    só PRs abertos antes da data, e só conta como decidido o que foi fechado antes dela.
    O parâmetro é interno ao fluxo orquestrado e não faz parte do esquema da consulta."""
    return consultar(f"""
        MATCH (u:User)-[:OPENED]->(p:PullRequest) WHERE {FILTRO_HUMANO}
          AND ($ate IS NULL OR p.created_at < datetime($ate))
        WITH p, CASE WHEN $ate IS NULL THEN p.state = 'closed'
                     ELSE p.closed_at IS NOT NULL AND p.closed_at < datetime($ate) END AS decidido
        WITH p, decidido,
             ($ate IS NULL OR decidido) AND (p.merged OR exists {{ (p)<-[:CLOSES]-(:Commit) }}) AS aceito
        RETURN p.author_association AS vinculo_autor, count(*) AS prs,
               sum(CASE WHEN NOT decidido THEN 1 ELSE 0 END) AS abertos,
               sum(CASE WHEN aceito THEN 1 ELSE 0 END) AS aceitos,
               sum(CASE WHEN aceito AND p.merged THEN 1 ELSE 0 END) AS via_merge_github,
               sum(CASE WHEN aceito AND NOT p.merged THEN 1 ELSE 0 END) AS via_commit_interno,
               sum(CASE WHEN decidido AND NOT aceito THEN 1 ELSE 0 END) AS fechados_sem_aceite,
               round(percentileCont(CASE WHEN p.closed_at IS NOT NULL AND ($ate IS NULL OR decidido)
                   THEN duration.inSeconds(p.created_at, p.closed_at).seconds / 86400.0 END, 0.5), 1)
                   AS dias_medianos_ate_decisao
        ORDER BY prs DESC
    """, ate=ate)


# ---------------------------------------------------------------------------
# Reserva: Cypher livre (Text2Cypher)
# ---------------------------------------------------------------------------

ESQUEMA_RESUMIDO = """Nós: User{id,login,name,type,contributions}, Issue{number,title,body,state,labels,created_at,closed_at},
PullRequest{number,title,body,state,merged,labels,author_association,created_at,closed_at},
Commit{sha,message,authored_at,in_dataset}, TimelineEvent{type,created_at,body}, Milestone{title},
CommitComment{body,path}, File{path}, Workflow{name}, Tag{name}, Release{name,published_at}, Branch{name}.
Relações: (User)-[:OPENED|ASSIGNED_TO]->(Issue|PullRequest), (User)-[:MERGED|REVIEWED]->(PullRequest),
(User)-[:AUTHORED|COMMITTED]->(Commit), (User)-[:PERFORMED]->(TimelineEvent)-[:ON]->(Issue|PullRequest),
(Issue|PullRequest)-[:MENTIONS]->(Issue|PullRequest), (Issue|PullRequest)-[:IN_MILESTONE]->(Milestone),
(PullRequest)-[:HAS_COMMIT|MERGED_AS]->(Commit), (Commit)-[:CLOSES|REFERENCES]->(Issue|PullRequest),
(CommitComment)-[:ON_COMMIT]->(Commit), (CommitComment)-[:ON_FILE]->(File), (Workflow)-[:RAN_ON]->(Commit),
(Tag|Branch)-[:POINTS_TO]->(Commit), (Release)-[:FROM_TAG]->(Tag).
Labels de issue/PR são strings como 'package=collect', 'type=defect', 'status=fixed'."""


@ferramenta(
    "Executa uma consulta Cypher somente leitura no grafo. Use apenas quando nenhuma "
    "outra ferramenta responder. Retorna no máximo 50 linhas. Esquema:\n" + ESQUEMA_RESUMIDO,
    {"cypher": {"type": "string", "description": "Consulta Cypher de leitura (MATCH ... RETURN)."}},
    ["cypher"],
)
def consultar_cypher(cypher: str) -> list[dict]:
    validar_cypher_livre(cypher)
    return consultar(cypher)[:50]
