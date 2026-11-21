"""Monta o grafo de conhecimento do google/guava no Neo4j a partir do SQLite.

O modelo está descrito em docs/modelo_grafo.md. Todas as escritas usam MERGE,
então o script é idempotente: rodar de novo atualiza propriedades sem duplicar.

Uso:
    uv run src/etl/carregar_grafo.py                  # todas as etapas
    uv run src/etl/carregar_grafo.py issues commits   # só algumas etapas
    uv run src/etl/carregar_grafo.py --limpar         # apaga o grafo antes
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from collections.abc import Callable

from neo4j import Driver

from comum import booleano, conectar_neo4j, conectar_sqlite, dt, executar_em_lotes, js

GUAVA = "/repos/google/guava/"

# ---------------------------------------------------------------------------
# Esquema: unicidade (cria índice junto) e índices auxiliares
# ---------------------------------------------------------------------------

CHAVES = {
    "User": "id",
    "Issue": "id",
    "PullRequest": "id",
    "Milestone": "id",
    "Commit": "sha",
    "CommitComment": "id",
    "File": "path",
    "Workflow": "id",
    "Branch": "name",
    "Tag": "name",
    "Release": "id",
    "TimelineEvent": "key",
    "Discussion": "id",
    "DiscussionComment": "id",
    "DiscussionCategory": "id",
}

INDICES = [
    ("Issue", "number"),
    ("PullRequest", "number"),
    ("User", "login"),
    ("TimelineEvent", "type"),
]


def criar_esquema(driver: Driver) -> None:
    for label, prop in CHAVES.items():
        driver.execute_query(
            f"CREATE CONSTRAINT {label.lower()}_{prop} IF NOT EXISTS "
            f"FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
        )
    for label, prop in INDICES:
        driver.execute_query(
            f"CREATE INDEX {label.lower()}_{prop} IF NOT EXISTS FOR (n:{label}) ON (n.{prop})"
        )
    # Busca por palavras em títulos e corpos (usada pela ferramenta buscar_discussoes)
    driver.execute_query(
        "CREATE FULLTEXT INDEX texto_issues_prs IF NOT EXISTS "
        "FOR (n:Issue|PullRequest) ON EACH [n.title, n.body]"
    )


# ---------------------------------------------------------------------------
# Etapas. Cada uma lê do SQLite e grava no Neo4j; retorna {descrição: linhas}.
# ---------------------------------------------------------------------------


def etapa_users(db, driver) -> dict[str, int]:
    linhas = [
        {
            "id": r["id"], "login": r["login"], "name": r["name"], "type": r["type"],
            "company": r["company"], "location": r["location"], "bio": r["bio"],
            "followers": r["followers"], "public_repos": r["public_repos"],
            "site_admin": booleano(r["site_admin"]), "created_at": dt(r["created_at"]),
        }
        for r in db.execute("SELECT * FROM users")
    ]
    n = executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MERGE (u:User {id: l.id}) SET u += l
    """, linhas)

    # contributors -> users: vira label extra + propriedade (não é entidade própria)
    contrib = [dict(r) for r in db.execute("SELECT user_id, contributions FROM contributors")]
    nc = executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MATCH (u:User {id: l.user_id})
        SET u:Contributor, u.contributions = l.contributions
    """, contrib)

    # watchers -> users (tabela vazia hoje, mas o código fica pronto)
    watch = [dict(r) for r in db.execute("SELECT user_id FROM watchers")]
    nw = executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MERGE (u:User {id: l.user_id}) SET u:Watcher
    """, watch)
    return {"User": n, "Contributor": nc, "Watcher": nw}


def etapa_milestones(db, driver) -> dict[str, int]:
    linhas = [
        {
            "id": r["id"], "number": r["number"], "title": r["title"], "state": r["state"],
            "description": r["description"], "open_issues": r["open_issues"],
            "closed_issues": r["closed_issues"], "created_at": dt(r["created_at"]),
            "closed_at": dt(r["closed_at"]), "due_on": dt(r["due_on"]),
        }
        for r in db.execute("SELECT * FROM milestones")
    ]
    return {"Milestone": executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MERGE (m:Milestone {id: l.id}) SET m += l
    """, linhas)}


def _props_issue(r) -> dict:
    """Propriedades comuns a Issue e PullRequest."""
    return {
        "id": r["id"], "number": r["number"], "title": r["title"], "body": r["body"],
        "state": r["state"], "labels": js(r["labels"]) or [], "locked": booleano(r["locked"]),
        "comments": r["comments"], "author_association": r["author_association"],
        "created_at": dt(r["created_at"]), "updated_at": dt(r["updated_at"]),
        "closed_at": dt(r["closed_at"]),
    }


def _relacoes_issue(db, tabela: str, label: str, driver) -> dict[str, int]:
    """users -> issue/PR (OPENED, ASSIGNED_TO) e issue/PR -> milestone."""
    autores, responsaveis, marcos = [], [], []
    for r in db.execute(f"SELECT id, user_id, assignees, milestone FROM {tabela}"):
        if r["user_id"]:
            autores.append({"id": r["id"], "user": r["user_id"]})
        responsaveis += [{"id": r["id"], "user": u} for u in js(r["assignees"]) or []]
        if m := js(r["milestone"]):
            marcos.append({"id": r["id"], "milestone": m["id"]})

    return {
        f"(User)-[:OPENED]->({label})": executar_em_lotes(driver, f"""
            UNWIND $linhas AS l
            MATCH (x:{label} {{id: l.id}}) MATCH (u:User {{id: l.user}})
            MERGE (u)-[:OPENED]->(x)
        """, autores),
        f"(User)-[:ASSIGNED_TO]->({label})": executar_em_lotes(driver, f"""
            UNWIND $linhas AS l
            MATCH (x:{label} {{id: l.id}}) MATCH (u:User {{id: l.user}})
            MERGE (u)-[:ASSIGNED_TO]->(x)
        """, responsaveis),
        f"({label})-[:IN_MILESTONE]->(Milestone)": executar_em_lotes(driver, f"""
            UNWIND $linhas AS l
            MATCH (x:{label} {{id: l.id}}) MATCH (m:Milestone {{id: l.milestone}})
            MERGE (x)-[:IN_MILESTONE]->(m)
        """, marcos),
    }


def etapa_issues(db, driver) -> dict[str, int]:
    linhas = []
    for r in db.execute("SELECT * FROM issues"):
        p = _props_issue(r)
        p["state_reason"] = r["state_reason"]
        linhas.append(p)
    n = executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MERGE (i:Issue {id: l.id}) SET i += l
    """, linhas)
    return {"Issue": n, **_relacoes_issue(db, "issues", "Issue", driver)}


def etapa_pull_requests(db, driver) -> dict[str, int]:
    linhas, merges, mergers = [], [], []
    for r in db.execute("SELECT * FROM pull_requests"):
        p = _props_issue(r)
        head, base = js(r["head"]) or {}, js(r["base"]) or {}
        p |= {
            "merged": booleano(r["merged"]), "merged_at": dt(r["merged_at"]),
            "draft": booleano(r["draft"]), "additions": r["additions"],
            "deletions": r["deletions"], "changed_files": r["changed_files"],
            "commit_count": r["commits"], "review_comments": r["review_comments"],
            "head_ref": head.get("ref"), "head_label": head.get("label"),
            "base_ref": base.get("ref"),
        }
        linhas.append(p)
        # merge_commit_sha também existe em PRs não mergeados (commit de teste do
        # GitHub), então só vale quando merged = 1.
        if r["merged"] and r["merge_commit_sha"]:
            merges.append({"id": r["id"], "sha": r["merge_commit_sha"]})
        if r["merged_by_id"]:
            mergers.append({"id": r["id"], "user": r["merged_by_id"]})

    n = executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MERGE (p:PullRequest {id: l.id}) SET p += l
    """, linhas)
    return {
        "PullRequest": n,
        **_relacoes_issue(db, "pull_requests", "PullRequest", driver),
        "(PullRequest)-[:MERGED_AS]->(Commit)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MATCH (p:PullRequest {id: l.id})
            MERGE (c:Commit {sha: l.sha})
            MERGE (p)-[:MERGED_AS]->(c)
        """, merges),
        "(User)-[:MERGED]->(PullRequest)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MATCH (p:PullRequest {id: l.id}) MATCH (u:User {id: l.user})
            MERGE (u)-[:MERGED]->(p)
        """, mergers),
    }


def etapa_commits(db, driver) -> dict[str, int]:
    linhas, autores, committers = [], [], []
    for r in db.execute("SELECT sha, author_id, committer_id, parents, \"commit\" FROM commits"):
        c = js(r["commit"]) or {}
        a, k = c.get("author") or {}, c.get("committer") or {}
        linhas.append({
            "sha": r["sha"], "message": c.get("message"),
            "author_name": a.get("name"), "author_email": a.get("email"),
            "authored_at": dt(a.get("date")),
            "committer_name": k.get("name"), "committed_at": dt(k.get("date")),
            "parents": js(r["parents"]) or [],
        })
        if r["author_id"]:
            autores.append({"sha": r["sha"], "user": r["author_id"]})
        if r["committer_id"]:
            committers.append({"sha": r["sha"], "user": r["committer_id"]})

    # in_dataset = true diferencia commits completos dos "stubs" criados só com o
    # sha por outras etapas (tags, workflows, PRs referenciam commits fora da tabela).
    n = executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MERGE (c:Commit {sha: l.sha}) SET c += l, c.in_dataset = true
    """, linhas)
    return {
        "Commit": n,
        "(User)-[:AUTHORED]->(Commit)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MATCH (c:Commit {sha: l.sha}) MATCH (u:User {id: l.user})
            MERGE (u)-[:AUTHORED]->(c)
        """, autores),
        "(User)-[:COMMITTED]->(Commit)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MATCH (c:Commit {sha: l.sha}) MATCH (u:User {id: l.user})
            MERGE (u)-[:COMMITTED]->(c)
        """, committers),
    }


def etapa_timeline(db, driver) -> dict[str, int]:
    """timeline_items -> issues/PRs.

    Cada item vira um nó TimelineEvent (guarda o histórico e o texto dos
    comentários para o guia contextual). Os tipos com significado estrutural também
    viram arestas diretas, mais fáceis de consultar.
    """
    issue_ids = {r[0] for r in db.execute("SELECT id FROM issues")}
    pr_por_numero = {r[0]: (r[1], r[2]) for r in db.execute("SELECT number, id, title FROM pull_requests")}

    eventos, has_commit, por_commit, reviews, xrefs = [], [], [], [], []
    for r in db.execute("SELECT * FROM timeline_items"):
        p = js(r["payload"]) or {}
        alvo = "Issue" if r["source"] == "issue" else "PullRequest"
        tipo = r["type"]
        evento = {
            "key": f"{r['source']}:{r['source_id']}:{r['position']}",
            "target": r["source_id"], "label": alvo,
            "props": {
                "type": tipo, "position": r["position"], "github_id": r["id"],
                "created_at": dt(p.get("created_at") or p.get("submitted_at")
                                 or (p.get("author") or {}).get("date")),
                "body": p.get("body"),
                "commit_id": p.get("commit_id") or p.get("sha"),
                "label_name": (p.get("label") or {}).get("name"),
                "review_state": p.get("state") if tipo == "reviewed" else None,
            },
            "actor": p.get("actor") or p.get("user"),
        }
        eventos.append(evento)

        if tipo == "committed" and p.get("sha"):
            has_commit.append({"pr": r["source_id"], "sha": p["sha"], "message": p.get("message")})
        elif tipo in ("referenced", "closed") and p.get("commit_id") \
                and GUAVA in (p.get("commit_url") or ""):  # ignora commits de forks
            por_commit.append({"sha": p["commit_id"], "target": r["source_id"], "label": alvo,
                               "rel": "CLOSES" if tipo == "closed" else "REFERENCES"})
        elif tipo == "reviewed" and p.get("user"):
            reviews.append({"pr": r["source_id"], "user": p["user"], "key": evento["key"],
                            "state": p.get("state"), "submitted_at": dt(p.get("submitted_at"))})
        elif tipo == "cross-referenced":
            # A origem vem como "issue" da API. Pode ser issue deste repo (casa por id),
            # PR deste repo (o id é o da issue interna do PR, então casa por número +
            # título) ou algo de outro repositório (ignorado).
            src = (p.get("source") or {}).get("issue") or {}
            if src.get("id") in issue_ids:
                origem = ("Issue", src["id"])
            elif (pr := pr_por_numero.get(src.get("number"))) and pr[1] == src.get("title"):
                origem = ("PullRequest", pr[0])
            else:
                continue
            xrefs.append({"src_label": origem[0], "src": origem[1], "dst_label": alvo,
                          "dst": r["source_id"], "at": evento["props"]["created_at"]})

    res = {}
    for label in ("Issue", "PullRequest"):
        do_label = [e for e in eventos if e["label"] == label]
        res[f"(TimelineEvent)-[:ON]->({label})"] = executar_em_lotes(driver, f"""
            UNWIND $linhas AS l
            MATCH (x:{label} {{id: l.target}})
            MERGE (e:TimelineEvent {{key: l.key}}) SET e += l.props
            MERGE (e)-[:ON]->(x)
        """, do_label)
    res["(User)-[:PERFORMED]->(TimelineEvent)"] = executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MATCH (e:TimelineEvent {key: l.key}) MATCH (u:User {id: l.actor})
        MERGE (u)-[:PERFORMED]->(e)
    """, [e for e in eventos if e["actor"]])

    res["(PullRequest)-[:HAS_COMMIT]->(Commit)"] = executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MATCH (p:PullRequest {id: l.pr})
        MERGE (c:Commit {sha: l.sha}) SET c.message = coalesce(c.message, l.message)
        MERGE (p)-[:HAS_COMMIT]->(c)
    """, has_commit)

    for label in ("Issue", "PullRequest"):
        for rel in ("CLOSES", "REFERENCES"):
            sel = [x for x in por_commit if x["label"] == label and x["rel"] == rel]
            res[f"(Commit)-[:{rel}]->({label})"] = executar_em_lotes(driver, f"""
                UNWIND $linhas AS l
                MATCH (x:{label} {{id: l.target}})
                MERGE (c:Commit {{sha: l.sha}})
                MERGE (c)-[:{rel}]->(x)
            """, sel)

    res["(User)-[:REVIEWED]->(PullRequest)"] = executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MATCH (p:PullRequest {id: l.pr}) MATCH (u:User {id: l.user})
        MERGE (u)-[r:REVIEWED {event_key: l.key}]->(p)
        SET r.state = l.state, r.submitted_at = l.submitted_at
    """, reviews)

    for s in ("Issue", "PullRequest"):
        for d in ("Issue", "PullRequest"):
            sel = [x for x in xrefs if x["src_label"] == s and x["dst_label"] == d]
            res[f"({s})-[:MENTIONS]->({d})"] = executar_em_lotes(driver, f"""
                UNWIND $linhas AS l
                MATCH (a:{s} {{id: l.src}}) MATCH (b:{d} {{id: l.dst}})
                MERGE (a)-[r:MENTIONS]->(b) SET r.at = l.at
            """, sel)
    return res


def etapa_commit_comments(db, driver) -> dict[str, int]:
    linhas = [
        {
            "id": r["id"], "sha": r["commit_id"], "user": r["user"], "path": r["path"],
            "props": {
                "id": r["id"], "body": r["body"], "path": r["path"], "line": r["line"],
                "position": r["position"], "author_association": r["author_association"],
                "created_at": dt(r["created_at"]), "updated_at": dt(r["updated_at"]),
            },
        }
        for r in db.execute("SELECT * FROM commit_comments")
    ]
    return {
        "(CommitComment)-[:ON_COMMIT]->(Commit)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MERGE (cc:CommitComment {id: l.id}) SET cc += l.props
            MERGE (c:Commit {sha: l.sha})
            MERGE (cc)-[:ON_COMMIT]->(c)
        """, linhas),
        "(User)-[:WROTE]->(CommitComment)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MATCH (cc:CommitComment {id: l.id}) MATCH (u:User {id: l.user})
            MERGE (u)-[:WROTE]->(cc)
        """, [x for x in linhas if x["user"]]),
        # file_path -> commit_comments
        "(CommitComment)-[:ON_FILE]->(File)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MATCH (cc:CommitComment {id: l.id})
            MERGE (f:File {path: l.path})
            MERGE (cc)-[:ON_FILE]->(f)
        """, [x for x in linhas if x["path"]]),
    }


def etapa_workflows(db, driver) -> dict[str, int]:
    wfs = [
        {"id": r["id"], "name": r["name"], "path": r["path"], "state": r["state"],
         "created_at": dt(r["created_at"])}
        for r in db.execute("SELECT * FROM workflows")
    ]
    n = executar_em_lotes(driver, """
        UNWIND $linhas AS l
        MERGE (w:Workflow {id: l.id}) SET w += l
    """, wfs)

    # workflows -> commits: as 5.714 execuções são agregadas por (workflow, commit)
    # numa única aresta RAN_ON com contadores, em vez de um nó por execução.
    agg: dict[tuple, dict] = defaultdict(lambda: {"runs": 0, "success": 0, "failure": 0,
                                                  "last_run_at": None, "last_conclusion": None})
    nomes = {}
    for r in db.execute("SELECT workflow_id, name, path, head_sha, conclusion, created_at "
                        "FROM workflow_runs ORDER BY created_at"):
        nomes[r["workflow_id"]] = {"name": r["name"], "path": r["path"]}
        a = agg[(r["workflow_id"], r["head_sha"])]
        a["runs"] += 1
        a["success"] += r["conclusion"] == "success"
        a["failure"] += r["conclusion"] == "failure"
        a["last_run_at"], a["last_conclusion"] = dt(r["created_at"]), r["conclusion"]
    runs = [{"wf": wf, "sha": sha, "props": a, **nomes[wf]} for (wf, sha), a in agg.items()]
    return {
        "Workflow": n,
        # Há execuções de workflows que não estão mais na tabela `workflows`
        # (removidos do repo); eles são criados com nome/caminho da execução.
        "(Workflow)-[:RAN_ON]->(Commit)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MERGE (w:Workflow {id: l.wf})
              ON CREATE SET w.name = l.name, w.path = l.path, w.state = 'removed'
            MERGE (c:Commit {sha: l.sha})
            MERGE (w)-[r:RAN_ON]->(c) SET r += l.props
        """, runs),
    }


def etapa_refs(db, driver) -> dict[str, int]:
    """branches, tags e releases."""
    branches = [{"name": r["name"], "sha": r["commit_sha"], "protected": booleano(r["protected"])}
                for r in db.execute("SELECT * FROM branches")]
    tags = [{"name": r["name"], "sha": r["commit_sha"]} for r in db.execute("SELECT * FROM tags")]
    releases = [
        {
            "tag": r["tag_name"],
            "props": {
                "id": r["id"], "tag_name": r["tag_name"], "name": r["name"], "body": r["body"],
                "draft": booleano(r["draft"]), "prerelease": booleano(r["prerelease"]),
                "target_commitish": r["target_commitish"],
                "created_at": dt(r["created_at"]), "published_at": dt(r["published_at"]),
            },
            "author": r["author_id"],
        }
        for r in db.execute("SELECT * FROM releases")
    ]
    return {
        "(Branch)-[:POINTS_TO]->(Commit)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MERGE (b:Branch {name: l.name}) SET b.protected = l.protected
            MERGE (c:Commit {sha: l.sha})
            MERGE (b)-[:POINTS_TO]->(c)
        """, branches),
        "(Tag)-[:POINTS_TO]->(Commit)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MERGE (t:Tag {name: l.name})
            MERGE (c:Commit {sha: l.sha})
            MERGE (t)-[:POINTS_TO]->(c)
        """, tags),
        "(Release)-[:FROM_TAG]->(Tag)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MERGE (r:Release {id: l.props.id}) SET r += l.props
            MERGE (t:Tag {name: l.tag})
            MERGE (r)-[:FROM_TAG]->(t)
            WITH r, l
            MATCH (u:User {id: l.author})
            MERGE (u)-[:PUBLISHED]->(r)
        """, releases),
    }


def etapa_discussions(db, driver) -> dict[str, int]:
    """discussions, categorias e comentários. As tabelas estão vazias neste dump;
    o código usa só colunas (não JSON), mas não pôde ser testado com dados reais."""
    cats = [{"id": r["node_id"], "name": r["name"], "description": r["description"],
             "is_answerable": booleano(r["is_answerable"])}
            for r in db.execute("SELECT * FROM discussion_categories")]
    discs = [
        {"category": r["category_id"], "author": r["author_id"],
         "props": {"id": r["node_id"], "number": r["number"], "title": r["title"],
                   "body": r["body"], "is_answered": booleano(r["is_answered"]),
                   "created_at": dt(r["created_at"]), "closed_at": dt(r["closed_at"])}}
        for r in db.execute("SELECT * FROM discussions")
    ]
    coms = [
        {"discussion": r["discussion_id"], "author": r["author_id"], "parent": r["parent_id"],
         "props": {"id": r["node_id"], "body": r["body"], "is_answer": booleano(r["is_answer"]),
                   "created_at": dt(r["created_at"])}}
        for r in db.execute("SELECT * FROM discussion_comments")
    ]
    return {
        "DiscussionCategory": executar_em_lotes(driver, """
            UNWIND $linhas AS l MERGE (c:DiscussionCategory {id: l.id}) SET c += l
        """, cats),
        "(Discussion)-[:IN_CATEGORY]->(DiscussionCategory)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MERGE (d:Discussion {id: l.props.id}) SET d += l.props
            WITH d, l
            OPTIONAL MATCH (c:DiscussionCategory {id: l.category})
            OPTIONAL MATCH (u:User {id: l.author})
            FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END | MERGE (d)-[:IN_CATEGORY]->(c))
            FOREACH (_ IN CASE WHEN u IS NULL THEN [] ELSE [1] END | MERGE (u)-[:OPENED]->(d))
        """, discs),
        "(DiscussionComment)-[:IN_DISCUSSION]->(Discussion)": executar_em_lotes(driver, """
            UNWIND $linhas AS l
            MERGE (dc:DiscussionComment {id: l.props.id}) SET dc += l.props
            WITH dc, l
            OPTIONAL MATCH (d:Discussion {id: l.discussion})
            OPTIONAL MATCH (u:User {id: l.author})
            FOREACH (_ IN CASE WHEN d IS NULL THEN [] ELSE [1] END | MERGE (dc)-[:IN_DISCUSSION]->(d))
            FOREACH (_ IN CASE WHEN u IS NULL THEN [] ELSE [1] END | MERGE (u)-[:WROTE]->(dc))
        """, coms),
    }


# Ordem importa só para preencher propriedades antes de criar "stubs";
# como tudo é MERGE, qualquer ordem gera o mesmo grafo final.
ETAPAS: dict[str, Callable] = {
    "users": etapa_users,
    "milestones": etapa_milestones,
    "issues": etapa_issues,
    "commits": etapa_commits,
    "pull_requests": etapa_pull_requests,
    "timeline": etapa_timeline,
    "commit_comments": etapa_commit_comments,
    "workflows": etapa_workflows,
    "refs": etapa_refs,
    "discussions": etapa_discussions,
}


def marcar_commits_referencia(driver: Driver) -> int:
    """Commits citados só como referência (sha vindo de tags, branches, workflows,
    PRs ou comentários) recebem in_dataset = false; os 7.503 da tabela `commits`
    recebem true na etapa de commits. Roda ao fim de qualquer carga (idempotente)."""
    r, _, _ = driver.execute_query(
        "MATCH (c:Commit) WHERE c.in_dataset IS NULL SET c.in_dataset = false RETURN count(c) AS n")
    return r[0]["n"]


def resumo(driver: Driver) -> None:
    nos, _, _ = driver.execute_query(
        "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY n DESC")
    rels, _, _ = driver.execute_query(
        "MATCH ()-[r]->() RETURN type(r) AS tipo, count(*) AS n ORDER BY n DESC")
    print("\nNós no grafo:")
    for r in nos:
        print(f"  {r['label']:<20} {r['n']:>7}")
    print("Relações no grafo:")
    for r in rels:
        print(f"  {r['tipo']:<20} {r['n']:>7}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("etapas", nargs="*", metavar="ETAPA",
                    help=f"etapas a rodar (padrão: todas). Opções: {', '.join(ETAPAS)}")
    ap.add_argument("--limpar", action="store_true", help="apaga todo o grafo antes de carregar")
    args = ap.parse_args()
    if invalidas := [e for e in args.etapas if e not in ETAPAS]:
        ap.error(f"etapa(s) desconhecida(s): {', '.join(invalidas)}")

    db = conectar_sqlite()
    with conectar_neo4j() as driver:
        if args.limpar:
            with driver.session() as s:
                s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 10000 ROWS").consume()
            print("Grafo apagado.")
        criar_esquema(driver)
        for nome in args.etapas or ETAPAS:
            t0 = time.perf_counter()
            res = ETAPAS[nome](db, driver)
            print(f"[{nome}] {time.perf_counter() - t0:.1f}s")
            for desc, n in res.items():
                print(f"    {desc:<55} {n:>6} linhas")
        print(f"[commits só de referência] in_dataset = false em {marcar_commits_referencia(driver)} novos")
        resumo(driver)


if __name__ == "__main__":
    main()
