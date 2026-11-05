"""Avaliação por replay histórico da recuperação de contexto (sem LLM).

Para cada issue, monta o contexto só com o que existia antes da sua criação
(guia.coletar_contexto(replay=True)) e compara com o que aconteceu depois:

- itens relacionados: issues/PRs anteriores ligados à issue por referência
  cruzada (MENTIONS, em qualquer direção)
- pessoas: quem comentou, revisou, fechou ou escreveu o commit que a resolveu
  (sem o autor e sem bots)

Baseline de pessoas: só os mantenedores ativos (quem mais fechou issues/PRs até a
data). O guia funde essa lista com quem atua na área da issue.

Uso:
    uv run avaliacao/replay.py              # todas as issues com gabarito
    uv run avaliacao/replay.py --limite 50  # amostra uniforme, para testes rápidos
    uv run avaliacao/replay.py --llm        # termos de busca vindos do LLM (lento na CPU)
Resultados por issue: data/avaliacao/replay_<data>.jsonl
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from statistics import mean

from kg_onboarding.grafo import FILTRO_HUMANO, RAIZ, consultar
from kg_onboarding.guia import (N_PESSOAS, N_SEMELHANTES, _issue, coletar_contexto, interpretar,
                               mantenedores_ativos)

SAIDA = RAIZ / "data" / "avaliacao"


def gabarito() -> list[dict]:
    return consultar(f"""
        MATCH (i:Issue)
        OPTIONAL MATCH (autor:User)-[:OPENED]->(i)
        CALL (i, autor) {{
            CALL (i) {{
                MATCH (u:User)-[:PERFORMED]->(e:TimelineEvent)-[:ON]->(i)
                WHERE e.type IN ['commented', 'closed', 'reviewed', 'merged'] RETURN u
              UNION
                MATCH (u:User)-[:AUTHORED]->(:Commit)-[:CLOSES]->(i) RETURN u
            }}
            WITH u WHERE {FILTRO_HUMANO} AND u <> coalesce(autor, u {{.id}})
            RETURN collect(DISTINCT u.login) AS pessoas
        }}
        RETURN i.number AS numero, pessoas,
               [(i)-[:MENTIONS]-(x) WHERE x.created_at < i.created_at | x.number] AS relacionados
        ORDER BY numero
    """)


def baseline_pessoas(numero: int, autor: str | None) -> list[str]:
    return [x["login"] for x in mantenedores_ativos(numero, autor, replay=True)]


def acerto(sugeridos: list, esperados: list, k: int) -> tuple[float, float]:
    """(hit@k, recall@k)"""
    top = set(sugeridos[:k])
    achados = top & set(esperados)
    return float(bool(achados)), len(achados) / len(esperados)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, help="avaliar uma amostra uniforme de N issues")
    ap.add_argument("--llm", action="store_true",
                    help="termos de busca da interpretação pelo LLM (Ollama) em vez da heurística")
    args = ap.parse_args()

    casos = [g for g in gabarito() if g["pessoas"] or g["relacionados"]]
    if args.limite:  # amostra espaçada uniformemente (os primeiros números são da migração)
        passo = max(1, len(casos) // args.limite)
        casos = casos[::passo][: args.limite]

    SAIDA.mkdir(parents=True, exist_ok=True)
    arquivo = SAIDA / f"replay_{'llm_' if args.llm else ''}{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    res = []
    with arquivo.open("w") as f:
        for n, g in enumerate(casos, 1):
            termos = None
            if args.llm:
                interp, _ = interpretar(_issue(g["numero"]))
                termos = list(dict.fromkeys(interp["termos_busca"] + interp["elementos_codigo"])) or None
            ctx = coletar_contexto(g["numero"], replay=True, termos=termos)
            sug_itens = [s["numero"] for s in ctx["itens_semelhantes"]]
            sug_pessoas = [p["login"] for p in ctx["pessoas"]]
            base = baseline_pessoas(g["numero"], ctx["issue"]["autor"])
            r = {"numero": g["numero"], "gabarito": g, "termos": ctx["termos_de_busca"],
                 "sugeridos": {"itens": sug_itens, "pessoas": sug_pessoas, "baseline": base}}
            if g["relacionados"]:
                r["itens_hit"], r["itens_recall"] = acerto(sug_itens, g["relacionados"], N_SEMELHANTES)
            if g["pessoas"]:
                r["pessoas_hit3"], _ = acerto(sug_pessoas, g["pessoas"], 3)
                r["pessoas_hit"], r["pessoas_recall"] = acerto(sug_pessoas, g["pessoas"], N_PESSOAS)
                r["base_hit3"], _ = acerto(base, g["pessoas"], 3)
                r["base_hit"], r["base_recall"] = acerto(base, g["pessoas"], N_PESSOAS)
            res.append(r)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            if n % 100 == 0:
                print(f"  {n}/{len(casos)} issues...")

    def media(chave: str) -> str:
        v = [r[chave] for r in res if chave in r]
        return f"{mean(v):.3f} (n={len(v)})" if v else "—"

    print(f"\nIssues avaliadas: {len(res)} · detalhes em {arquivo.relative_to(RAIZ)}\n")
    print(f"Itens relacionados  hit@{N_SEMELHANTES}: {media('itens_hit')}  recall@{N_SEMELHANTES}: {media('itens_recall')}")
    print(f"Pessoas (guia)      hit@3: {media('pessoas_hit3')}  hit@{N_PESSOAS}: {media('pessoas_hit')}  recall@{N_PESSOAS}: {media('pessoas_recall')}")
    print(f"Pessoas (baseline)  hit@3: {media('base_hit3')}  hit@{N_PESSOAS}: {media('base_hit')}  recall@{N_PESSOAS}: {media('base_recall')}")


if __name__ == "__main__":
    main()
