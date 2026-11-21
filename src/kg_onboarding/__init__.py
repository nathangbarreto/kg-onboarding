"""kg-onboarding: grafo de conhecimento para apoiar newcomers em projetos open source.

Dois comandos:

    kgo <issue> [--sem-llm] [--json] [--offline]     # guia contextual de uma issue (atalho)
    kgo --lista                                      # issues com guia contextual pré-gerado

    kg-onboarding                                    # lista o catálogo de consultas
    kg-onboarding <consulta> [param=valor ...]       # executa uma consulta do catálogo
    kg-onboarding guia <issue> [--sem-llm] [--snapshot] [--json] [--offline]
    kg-onboarding exportar <issue> [<issue> ...]     # gera guias contextuais para o modo offline

O guia contextual é produzido pelo fluxo orquestrado de kg_onboarding.guia.
Modo ao vivo: com o Neo4j configurado e acessível, o guia é montado na hora.
Modo offline: sem Neo4j (ex.: testadores), usa os guias contextuais pré-gerados em
kg_onboarding/dados/guias.json, distribuídos dentro do pacote.
--snapshot (sinônimo antigo: --replay): análise por snapshot, só com o que existia
na data de criação da issue.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

USO_GUIA = "[--sem-llm] [--snapshot] [--json] [--offline]"


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "guia":
        _guia(args[1:])
        return
    if args and args[0] == "exportar":
        _exportar(args[1:])
        return

    from kg_onboarding.ferramentas import FERRAMENTAS

    if not args or args[0] in ("-h", "--help"):
        print(f"Uso: kg-onboarding guia <issue> {USO_GUIA}   (atalho: kgo <issue>)")
        print("     kg-onboarding exportar <issue> [<issue> ...]")
        print("     kg-onboarding <consulta> [param=valor ...]\n\nCatálogo de consultas:")
        for f in FERRAMENTAS.values():
            props = f.parametros["properties"]
            obrig = set(f.parametros["required"])
            assinatura = " ".join(f"{p}=…" if p in obrig else f"[{p}=…]" for p in props)
            print(f"  {f.nome} {assinatura}\n      {f.descricao.splitlines()[0]}")
        return

    nome, pares = args[0], args[1:]
    if nome not in FERRAMENTAS:
        sys.exit(f"Consulta desconhecida: {nome}. Rode sem argumentos para ver o catálogo.")
    f = FERRAMENTAS[nome]
    kwargs = {}
    for par in pares:
        chave, _, valor = par.partition("=")
        tipo = f.parametros["properties"].get(chave, {}).get("type")
        if tipo is None:
            sys.exit(f"Parâmetro desconhecido para {nome}: {chave}")
        kwargs[chave] = int(valor) if tipo == "integer" else valor
    print(json.dumps(f(**kwargs), ensure_ascii=False, indent=2))


def kgo() -> None:
    """Atalho para newcomers: kgo <issue>."""
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(f"Uso: kgo <número da issue> {USO_GUIA}\n     kgo --lista\n")
        print("Mostra um guia contextual para começar a trabalhar em uma issue do google/guava:")
        print("resumo, discussões relacionadas, onde investigar no código, com quem falar")
        print("e como contribuir.")
        return
    if args[0] == "--lista":
        _listar_offline()
        return
    _guia(args)


# ---------------------------------------------------------------------------
# Guia de uma issue
# ---------------------------------------------------------------------------

def _guia(args: list[str]) -> None:
    from kg_onboarding import grafo

    numeros = [a for a in args if not a.startswith("--")]
    if len(numeros) != 1 or not numeros[0].lstrip("#").isdigit():
        sys.exit(f"Uso: kgo <número da issue> {USO_GUIA}")
    numero = int(numeros[0].lstrip("#"))
    sem_llm, como_json = "--sem-llm" in args, "--json" in args
    replay = "--snapshot" in args or "--replay" in args  # análise por snapshot

    if "--offline" in args or not grafo.disponivel():
        resultado = _guia_offline(numero, replay)
        if sem_llm:
            resultado = resultado["contexto"]
    else:
        resultado = _guia_ao_vivo(numero, replay, sem_llm)
        sem_llm = "guia" not in resultado
    _imprimir(numero, resultado, sem_llm, como_json)


def _guia_ao_vivo(numero: int, replay: bool, sem_llm: bool) -> dict[str, Any]:
    from kg_onboarding import llm
    from kg_onboarding.guia import coletar_contexto, gerar_guia

    if not sem_llm and not llm.disponivel():
        print(f"LLM indisponível ({llm.PROVEDOR}); mostrando só o contexto do grafo.", file=sys.stderr)
        sem_llm = True
    if sem_llm:
        resultado = coletar_contexto(numero, replay=replay)
    else:
        print(f"Gerando guia contextual com {llm.MODELO} ({llm.PROVEDOR})...", file=sys.stderr)
        resultado = gerar_guia(numero, replay=replay)
    if resultado is None:
        sys.exit(f"Issue #{numero} não encontrada no grafo.")
    return resultado


def _carregar_offline() -> dict[str, Any]:
    from kg_onboarding.config import ARQUIVO_GUIAS

    if not ARQUIVO_GUIAS.is_file():
        sys.exit("Sem conexão com o grafo e sem guias contextuais pré-gerados neste pacote.")
    return json.loads(ARQUIVO_GUIAS.read_text(encoding="utf-8"))


def _guia_offline(numero: int, replay: bool) -> dict[str, Any]:
    if replay:
        sys.exit("--snapshot precisa do grafo (modo ao vivo).")
    pacote = _carregar_offline()
    resultado = pacote["guias"].get(str(numero))
    if resultado is None:
        disponiveis = ", ".join(sorted(pacote["guias"], key=int))
        sys.exit(f"Não há guia contextual pré-gerado para a issue #{numero}.\n"
                 f"Issues disponíveis: {disponiveis}\n(kgo --lista mostra os títulos)")
    print(f"(modo offline: guia contextual gerado em {pacote['meta']['gerado_em'][:10]})", file=sys.stderr)
    return resultado


def _listar_offline() -> None:
    pacote = _carregar_offline()
    print(f"Guias contextuais pré-gerados ({len(pacote['guias'])}), modelo {pacote['meta']['modelo']}:\n")
    for n, g in sorted(pacote["guias"].items(), key=lambda kv: int(kv[0])):
        print(f"  {n:>5}  {g['contexto']['issue']['titulo'][:90]}")


def _imprimir(numero: int, resultado: dict[str, Any], sem_llm: bool, como_json: bool) -> None:
    from kg_onboarding.guia import formatar_markdown

    if como_json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
        return
    if sem_llm:
        print(formatar_markdown(resultado))
        return
    m = resultado["metricas"]
    i = resultado["contexto"]["issue"]
    print(f"# Guia contextual para a issue #{numero}: {i['titulo']}\n")
    print(resultado["guia"])
    s = m["sintese"].get("original", m["sintese"])
    print(f"\n---\n_Modelo {s['modelo']} · {s['tokens_entrada']} tokens de entrada na síntese"
          f"{_custo(m)} · termos: {', '.join(resultado['contexto']['termos_de_busca'])}_")


def _custo(metricas: dict) -> str:
    custos = [m.get("original", m).get("custo_usd") for m in metricas.values()]
    return "" if None in custos else f" · custo US$ {sum(custos):.4f}"


# ---------------------------------------------------------------------------
# Exportação para o modo offline
# ---------------------------------------------------------------------------

def _exportar(args: list[str]) -> None:
    """Gera (ou atualiza) os guias contextuais pré-gerados distribuídos no pacote."""
    from kg_onboarding import grafo, llm
    from kg_onboarding.config import ARQUIVO_GUIAS
    from kg_onboarding.guia import gerar_guia

    if not args or not all(a.isdigit() for a in args):
        sys.exit("Uso: kg-onboarding exportar <issue> [<issue> ...]")
    if not grafo.disponivel() or not llm.disponivel():
        sys.exit("Exportar precisa do grafo e do LLM (modo ao vivo).")

    pacote = (json.loads(ARQUIVO_GUIAS.read_text(encoding="utf-8"))
              if ARQUIVO_GUIAS.is_file() else {"guias": {}})
    custo = 0.0
    for a in args:
        r = gerar_guia(int(a))
        if r is None:
            print(f"  #{a}: não encontrada no grafo", file=sys.stderr)
            continue
        c = sum(m.get("custo_usd") or 0 for m in r["metricas"].values())
        custo += c
        pacote["guias"][a] = r
        print(f"  #{a}: ok (US$ {c:.4f}{', cache' if c == 0 else ''})", file=sys.stderr)
    pacote["meta"] = {"gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "modelo": llm.MODELO, "provedor": llm.PROVEDOR, "issues": len(pacote["guias"])}
    ARQUIVO_GUIAS.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_GUIAS.write_text(json.dumps(pacote, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(pacote['guias'])} guias contextuais em {ARQUIVO_GUIAS} · custo desta exportação US$ {custo:.4f}",
          file=sys.stderr)
