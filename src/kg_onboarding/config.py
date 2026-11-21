"""Configuração e caminhos, funcionando tanto no clone do repositório quanto instalado.

Ordem de leitura das variáveis (a primeira definição vence):
1. variáveis de ambiente do shell
2. .env no diretório atual
3. .env na raiz do clone (quando o pacote roda a partir do repositório)
4. ~/.config/kg-onboarding/.env
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PACOTE = Path(__file__).resolve().parent
_raiz = PACOTE.parents[1]
# No clone, src/kg_onboarding fica dois níveis abaixo do pyproject.toml; instalado, não.
RAIZ: Path | None = _raiz if (_raiz / "pyproject.toml").exists() else None

DIR_CONFIG = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config")) / "kg-onboarding"
# Dados gerados (cache do LLM etc.): data/ no clone; ~/.cache/kg-onboarding instalado.
DIR_DADOS = (RAIZ / "data") if RAIZ else Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "kg-onboarding"

# Guias pré-gerados distribuídos dentro do pacote (modo offline).
ARQUIVO_GUIAS = PACOTE / "dados" / "guias.json"

for _env in (Path.cwd() / ".env", RAIZ / ".env" if RAIZ else None, DIR_CONFIG / ".env"):
    if _env and _env.is_file():
        load_dotenv(_env)  # não sobrescreve o que já foi definido


def neo4j_configurado() -> bool:
    return all(os.getenv(v) for v in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"))
