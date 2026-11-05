"""kg-onboarding: grafo de conhecimento para apoiar newcomers em projetos open source."""

from __future__ import annotations

import json
import sys


def main() -> None:
    """CLI para chamar as ferramentas do agente diretamente.

    uv run kg-onboarding                                # lista as ferramentas
    uv run kg-onboarding especialistas_do_pacote pacote=cache limite=5
    uv run kg-onboarding guia 2098 [--sem-llm] [--replay] [--json]  # guia de uma issue
    """
    from kg_onboarding.ferramentas import FERRAMENTAS

    args = sys.argv[1:]
    if args and args[0] == "guia":
        _guia(args[1:])
        return
    if not args or args[0] in ("-h", "--help"):
        print("Uso: kg-onboarding guia <numero> [--sem-llm] [--replay] [--json]")
        print("     kg-onboarding <ferramenta> [param=valor ...]\n\nFerramentas:")
        for f in FERRAMENTAS.values():
            props = f.parametros["properties"]
            obrig = set(f.parametros["required"])
            assinatura = " ".join(f"{p}=…" if p in obrig else f"[{p}=…]" for p in props)
            print(f"  {f.nome} {assinatura}\n      {f.descricao.splitlines()[0]}")
        return

    nome, pares = args[0], args[1:]
    if nome not in FERRAMENTAS:
        sys.exit(f"Ferramenta desconhecida: {nome}. Rode sem argumentos para ver a lista.")
    f = FERRAMENTAS[nome]
    kwargs = {}
    for par in pares:
        chave, _, valor = par.partition("=")
        tipo = f.parametros["properties"].get(chave, {}).get("type")
        if tipo is None:
            sys.exit(f"Parâmetro desconhecido para {nome}: {chave}")
        kwargs[chave] = int(valor) if tipo == "integer" else valor
    print(json.dumps(f(**kwargs), ensure_ascii=False, indent=2))


def _guia(args: list[str]) -> None:
    """Guia de uma issue. Padrão: fluxo completo com o LLM local (Ollama).

    --sem-llm  só recuperação e consolidação (rápido, sem Ollama)
    --replay   só informações anteriores à criação da issue
    --json     saída estruturada completa
    """
    from kg_onboarding.guia import coletar_contexto, formatar_markdown, gerar_guia

    numeros = [a for a in args if not a.startswith("--")]
    if len(numeros) != 1 or not numeros[0].isdigit():
        sys.exit("Uso: kg-onboarding guia <numero> [--sem-llm] [--replay] [--json]")
    numero, replay = int(numeros[0]), "--replay" in args

    if "--sem-llm" in args:
        resultado = coletar_contexto(numero, replay=replay)
    else:
        print("Gerando guia com o LLM local (pode levar 1-2 min na CPU)...", file=sys.stderr)
        resultado = gerar_guia(numero, replay=replay)
    if resultado is None:
        sys.exit(f"Issue #{numero} não encontrada no grafo.")

    if "--json" in args:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    elif "--sem-llm" in args:
        print(formatar_markdown(resultado))
    else:
        m = resultado["metricas"]
        i = resultado["contexto"]["issue"]
        print(f"# Guia para a issue #{numero}: {i['titulo']}\n")
        print(resultado["guia"])
        print(f"\n---\n_Modelo {m['sintese']['modelo']} · interpretação {m['interpretacao']['segundos']} s"
              f" · síntese {m['sintese']['segundos']} s ({m['sintese']['tokens_entrada']} tokens de"
              f" entrada) · termos: {', '.join(resultado['contexto']['termos_de_busca'])}_")
