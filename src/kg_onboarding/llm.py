"""Acesso ao LLM local (Ollama). Configuração no .env:

LLM_MODELO=qwen2.5:3b              # modelo baixado no Ollama
OLLAMA_HOST=http://localhost:11434
LLM_NUM_CTX=8192                   # janela de contexto (o padrão do Ollama é pequeno
                                   # e corta o excesso sem avisar)
"""

from __future__ import annotations

import json
import os
import sys
import time
from functools import cache
from typing import Any

import ollama
from dotenv import load_dotenv

from kg_onboarding.grafo import RAIZ

load_dotenv(RAIZ / ".env")
MODELO = os.getenv("LLM_MODELO", "qwen2.5:3b")
NUM_CTX = int(os.getenv("LLM_NUM_CTX", "8192"))
# temperatura 0 e semente fixa: mesma entrada -> mesma saída (reprodutibilidade)
OPCOES = {"temperature": 0, "seed": 42, "num_ctx": NUM_CTX}


@cache
def _cliente() -> ollama.Client:
    return ollama.Client(host=os.getenv("OLLAMA_HOST", "http://localhost:11434"))


def gerar(sistema: str, usuario: str, esquema: dict | None = None) -> tuple[str, dict[str, Any]]:
    """Uma chamada ao modelo. Retorna (texto, métricas da chamada)."""
    inicio = time.perf_counter()
    r = _cliente().chat(
        model=MODELO,
        messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
        format=esquema,
        options=OPCOES,
    )
    metricas = {
        "modelo": MODELO,
        "tokens_entrada": r.prompt_eval_count,
        "tokens_saida": r.eval_count,
        "segundos": round(time.perf_counter() - inicio, 1),
    }
    if r.prompt_eval_count and r.prompt_eval_count >= NUM_CTX:
        print(f"AVISO: prompt com {r.prompt_eval_count} tokens atingiu LLM_NUM_CTX={NUM_CTX}; "
              "o início pode ter sido cortado.", file=sys.stderr)
    return r.message.content, metricas


def gerar_json(sistema: str, usuario: str, esquema: dict) -> tuple[dict, dict[str, Any]]:
    """Chamada com saída restrita a um JSON Schema."""
    texto, metricas = gerar(sistema, usuario, esquema)
    return json.loads(texto), metricas
