"""Acesso ao LLM. Dois provedores, mesma interface (gerar / gerar_json).

Configuração no .env:

LLM_PROVEDOR=anthropic             # "anthropic" (API do Claude) ou "ollama" (local).
                                   # Padrão: anthropic se houver ANTHROPIC_API_KEY, senão ollama.
LLM_MODELO=...                     # padrão: claude-haiku-5-5 (anthropic) / qwen2.5:3b (ollama)

# só anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_ESFORCO=low                    # low | medium | high: profundidade do raciocínio (e custo)
LLM_PENSAR=0                       # 1 liga o raciocínio (thinking) do Haiku; desligado é mais barato
LLM_CACHE=1                        # 0 desliga o cache de respostas em data/cache_llm/

# só ollama
OLLAMA_HOST=http://localhost:11434
LLM_NUM_CTX=8192                   # janela de contexto (o padrão do Ollama é pequeno
                                   # e corta o excesso sem avisar)

Reprodutibilidade: no Ollama, temperatura 0 e semente fixa. Os modelos atuais do
Claude não aceitam temperatura nem semente; a variação é contida pelo esquema JSON
na interpretação, e cada resposta fica registrada nas métricas (modelo, tokens, custo).

Economia: respostas ficam em cache no disco (data/cache_llm/ no clone), indexadas pelo hash de
provedor + modelo + configuração + prompts; a mesma entrada não é paga duas vezes e
devolve exatamente a mesma resposta (o que também ajuda a reprodutibilidade).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from functools import cache
from typing import Any

from kg_onboarding.config import DIR_DADOS

PROVEDOR = os.getenv("LLM_PROVEDOR") or ("anthropic" if os.getenv("ANTHROPIC_API_KEY") else "ollama")
if PROVEDOR not in ("anthropic", "ollama"):
    sys.exit(f"LLM_PROVEDOR inválido: {PROVEDOR!r} (use 'anthropic' ou 'ollama').")

MODELO = os.getenv("LLM_MODELO") or ("claude-haiku-5-5" if PROVEDOR == "anthropic" else "qwen2.5:3b")

# --- Anthropic ---------------------------------------------------------------
ESFORCO = os.getenv("LLM_ESFORCO", "low")
# Raciocínio desligado: menos tokens de saída (os mais caros). Só o Haiku aceita
# desligar com {"type": "disabled"}; nos outros modelos o parâmetro não é enviado.
PENSAR = os.getenv("LLM_PENSAR", "0") == "1"
MAX_TOKENS = 4000  # teto de segurança; paga-se só o que for gerado
# US$ por milhão de tokens (entrada, saída), prompts até 100 mil tokens. Conferir em
# https://platform.claude.com/docs/en/about-claude/pricing antes de relatar custos.
PRECOS = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00),
          "claude-opus-5-5": (4.00, 20.00)}

CACHE = os.getenv("LLM_CACHE", "1") == "1"
DIR_CACHE = DIR_DADOS / "cache_llm"

# --- Ollama ------------------------------------------------------------------
NUM_CTX = int(os.getenv("LLM_NUM_CTX", "8192"))
# temperatura 0 e semente fixa: mesma entrada -> mesma saída (reprodutibilidade)
OPCOES_OLLAMA = {"temperature": 0, "seed": 42, "num_ctx": NUM_CTX}


@cache
def _cliente():
    if PROVEDOR == "anthropic":
        import anthropic
        return anthropic.Anthropic()  # lê ANTHROPIC_API_KEY do ambiente (.env já carregado)
    import ollama
    return ollama.Client(host=os.getenv("OLLAMA_HOST", "http://localhost:11434"))


def disponivel() -> bool:
    """O provedor configurado está acessível? (usado para pular testes)."""
    try:
        if PROVEDOR == "anthropic":
            return bool(os.getenv("ANTHROPIC_API_KEY"))
        return any(m.model.startswith(MODELO) for m in _cliente().list().models)
    except Exception:  # noqa: BLE001
        return False


def _esquema_estrito(esquema: dict) -> dict:
    """Saída estruturada da API exige additionalProperties: false nos objetos."""
    e = dict(esquema)
    if e.get("type") == "object":
        e.setdefault("additionalProperties", False)
        e["properties"] = {k: _esquema_estrito(v) for k, v in e.get("properties", {}).items()}
    if e.get("type") == "array" and isinstance(e.get("items"), dict):
        e["items"] = _esquema_estrito(e["items"])
    return e


def _gerar_anthropic(sistema: str, usuario: str, esquema: dict | None) -> tuple[str, dict[str, Any]]:
    output_config: dict[str, Any] = {"effort": ESFORCO}
    if esquema is not None:
        output_config["format"] = {"type": "json_schema", "schema": _esquema_estrito(esquema)}
    extras: dict[str, Any] = {}
    if not PENSAR and MODELO.startswith("claude-haiku"):
        extras["thinking"] = {"type": "disabled"}
    r = _cliente().messages.create(
        model=MODELO,
        max_tokens=MAX_TOKENS,
        system=sistema,
        messages=[{"role": "user", "content": usuario}],
        output_config=output_config,
        **extras,
    )
    if r.stop_reason == "refusal":
        raise RuntimeError(f"O modelo recusou a solicitação: {r.stop_details}")
    if r.stop_reason == "max_tokens":
        print(f"AVISO: resposta cortada em max_tokens={MAX_TOKENS}.", file=sys.stderr)
    texto = "".join(b.text for b in r.content if b.type == "text")
    entrada, saida = r.usage.input_tokens, r.usage.output_tokens
    preco = PRECOS.get(MODELO)
    return texto, {
        "provedor": PROVEDOR,
        "modelo": MODELO,
        "esforco": ESFORCO,
        "pensar": PENSAR,
        "tokens_entrada": entrada,
        "tokens_saida": saida,
        "custo_usd": round((entrada * preco[0] + saida * preco[1]) / 1e6, 6) if preco else None,
    }


def _gerar_ollama(sistema: str, usuario: str, esquema: dict | None) -> tuple[str, dict[str, Any]]:
    r = _cliente().chat(
        model=MODELO,
        messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
        format=esquema,
        options=OPCOES_OLLAMA,
    )
    if r.prompt_eval_count and r.prompt_eval_count >= NUM_CTX:
        print(f"AVISO: prompt com {r.prompt_eval_count} tokens atingiu LLM_NUM_CTX={NUM_CTX}; "
              "o início pode ter sido cortado.", file=sys.stderr)
    return r.message.content, {
        "provedor": PROVEDOR,
        "modelo": MODELO,
        "tokens_entrada": r.prompt_eval_count,
        "tokens_saida": r.eval_count,
    }


def _chave_cache(sistema: str, usuario: str, esquema: dict | None) -> str:
    config = {"provedor": PROVEDOR, "modelo": MODELO, "sistema": sistema, "usuario": usuario,
              "esquema": esquema}
    if PROVEDOR == "anthropic":
        config |= {"esforco": ESFORCO, "pensar": PENSAR}
    else:
        config |= {"opcoes": OPCOES_OLLAMA}
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def gerar(sistema: str, usuario: str, esquema: dict | None = None) -> tuple[str, dict[str, Any]]:
    """Uma chamada ao modelo (ou ao cache). Retorna (texto, métricas da chamada)."""
    arquivo = DIR_CACHE / f"{_chave_cache(sistema, usuario, esquema)}.json"
    if CACHE and arquivo.exists():
        salvo = json.loads(arquivo.read_text())
        # custo zero: a resposta já foi paga; as métricas originais ficam em "original"
        return salvo["texto"], {**salvo["metricas"], "cache": True, "custo_usd": 0.0,
                                "segundos": 0.0, "original": salvo["metricas"]}
    inicio = time.perf_counter()
    fn = _gerar_anthropic if PROVEDOR == "anthropic" else _gerar_ollama
    texto, metricas = fn(sistema, usuario, esquema)
    metricas["segundos"] = round(time.perf_counter() - inicio, 1)
    if CACHE:
        DIR_CACHE.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps({"texto": texto, "metricas": metricas}, ensure_ascii=False))
    return texto, {**metricas, "cache": False}


def gerar_json(sistema: str, usuario: str, esquema: dict) -> tuple[dict, dict[str, Any]]:
    """Chamada com saída restrita a um JSON Schema."""
    texto, metricas = gerar(sistema, usuario, esquema)
    return json.loads(texto), metricas
