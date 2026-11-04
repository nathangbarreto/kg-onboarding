"""kg-onboarding: grafo de conhecimento para apoiar newcomers em projetos open source."""

from __future__ import annotations

import json
import sys


def main() -> None:
    """CLI para chamar as ferramentas do agente diretamente.

    uv run kg-onboarding                                # lista as ferramentas
    uv run kg-onboarding especialistas_do_pacote pacote=cache limite=5
    """
    from kg_onboarding.ferramentas import FERRAMENTAS

    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("Uso: kg-onboarding <ferramenta> [param=valor ...]\n\nFerramentas:")
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
