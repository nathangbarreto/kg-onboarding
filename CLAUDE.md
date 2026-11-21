# kg-onboarding

Projeto de mestrado: construir um grafo de conhecimento (Neo4j) a partir de um banco
SQLite com dados extraídos do repositório GitHub `google/guava`. Um fluxo orquestrado
consulta o grafo e, com um LLM, gera guias contextuais para apoiar newcomers.

O texto de qualificação (revisado pelos orientadores) é a referência: o repositório
deve estar conforme ao que ele afirma. Terminologia em docs, docstrings e mensagens:
"guia contextual", "grafo de conhecimento", "catálogo de consultas", "fluxo orquestrado",
"análise por snapshot" (não "replay") e os nomes das etapas do diagrama (Interpretar a
issue, Enriquecer contexto da issue, Buscar itens semelhantes, Identificar pessoas
relacionadas, Localizar elementos de código, Recuperar processo de contribuição,
Consolidar e priorizar o contexto, Sintetizar guia contextual). Previstos no texto e NÃO
implementados (não implementar nem anunciar como prontos): modo agente, servidor MCP,
mineração do histórico Git (relação commit–arquivo) e comparação sistemática entre LLMs.
Números citados no texto (manter): 43.826 nós, 77.097 relações, 33 testes, 9 consultas
curadas + 1 livre.

## Estrutura

```
data/raw/guava.sqlite   banco de origem (issues etc.; 1446 linhas em issues)
data/repos/             clones de repositórios (ex.: para PyDriller)
neo4j/                  dados e logs do Neo4j (volume do Docker)
src/etl/                scripts de ETL SQLite -> Neo4j
src/kg_onboarding/      pacote Python do projeto
notebooks/              análises exploratórias (Jupyter)
docs/                   documentação
docker-compose.yml      Neo4j 5.26 Community + plugin Graph Data Science
.env                    credenciais e configuração (não versionado; modelo em .env.example)
```

## Neo4j

- Subir: `docker compose up -d` (aguarde "Started." em `docker logs neo4j-mestrado`)
- Parar: `docker compose down` (os dados ficam em `neo4j/data`)
- Browser: http://localhost:7474 · Bolt: `bolt://localhost:7687`
- Senha: em `.env`. Não exibir nem commitar. Ex. de uso no shell sem mostrar:
  `docker exec neo4j-mestrado cypher-shell -u neo4j -p "$(grep ^NEO4J_PASSWORD .env | cut -d= -f2-)" "RETURN gds.version();"`
- Não alterar a versão das imagens (`neo4j:5.26-community`, `ollama/ollama:0.40.14`) sem combinar antes.

## Python (uv)

- Python 3.12 (fixado em `.python-version`); dependências em `pyproject.toml`.
- Rodar scripts: `uv run src/etl/<script>.py`
- Adicionar pacote: `uv add <pacote>` (no zsh, use aspas em extras: `uv add "mcp[cli]"`)
- Teste do ambiente: `uv run src/etl/teste_conexao.py`
- Carga do grafo: `uv run src/etl/carregar_grafo.py [etapas...] [--limpar]`
  (modelo e decisões em `docs/modelo_grafo.md`; utilitários em `src/etl/comum.py`)
- Catálogo de consultas: `src/kg_onboarding/ferramentas.py` (registrar com `@ferramenta`,
  sempre via `grafo.consultar`, que roda em modo leitura); CLI `uv run kg-onboarding`
- Testes: `uv run pytest` (integração; precisa do Neo4j com o grafo carregado). Ao criar
  ferramenta, acrescente perguntas em `avaliacao/perguntas.jsonl`.
- Dados de issues/PRs estão incompletos (extração abortada); ver `docs/perguntas_newcomer.md`.
- Guia contextual de uma issue (fluxo orquestrado): `src/kg_onboarding/guia.py`;
  `uv run kg-onboarding guia <n> [--sem-llm] [--snapshot] [--json]` (`--replay` é sinônimo antigo). Mapa e resultados: `docs/arquitetura.md`
- LLM: acesso só via `src/kg_onboarding/llm.py`, que tem dois provedores com a mesma
  interface (`gerar`/`gerar_json`). Padrão: API do Claude (`claude-haiku-5-5`, o mais barato)
  se houver `ANTHROPIC_API_KEY` no `.env`; senão, Ollama local (`qwen2.5:3b`, temperatura 0,
  seed fixa, `num_ctx` explícito). Os modelos atuais do Claude rejeitam `temperature`: não
  enviar. O Ollama fica no perfil `ollama` do compose (`docker compose --profile ollama up -d`).
  Variáveis em `.env.example`. Economia (pedido do usuário): raciocínio do Haiku desligado
  (`LLM_PENSAR=0`), esforço `low` e cache de respostas em `data/cache_llm/` — não desligar
  sem combinar. Chave da API só no `.env` (permissão 600); nunca exibir, logar ou commitar.
- Configuração: `src/kg_onboarding/config.py` (env, `./.env`, `.env` do clone,
  `~/.config/kg-onboarding/.env`); funciona no clone e instalado via `.whl`.
- MVP para testes: `kgo <issue>` (atalho). Sem Neo4j, usa os guias pré-gerados em
  `src/kg_onboarding/dados/guias.json` (`uv run kg-onboarding exportar <issues...>`,
  depois `uv build --wheel`). Não gerar guias para todas as issues sem o usuário pedir.
  Instruções para testadores: `docs/teste_mvp.md`.
- Dependências: núcleo enxuto (neo4j, anthropic, python-dotenv); `ollama` é extra
  (`uv sync --extra ollama`); análise (jupyter, polars, duckdb, pydriller) no grupo `analise`.
- Análise por snapshot (exploratória): `uv run avaliacao/replay.py` (resultados em `data/avaliacao/`).
  Ao mudar a recuperação, rode de novo e atualize a tabela em `docs/arquitetura.md`.
- Mineração do repositório Git: adiada por decisão do usuário; não implementar sem pedido.
- Se houver um `VIRTUAL_ENV` de outro projeto ativo, o uv avisa e o ignora; pode-se rodar `unset VIRTUAL_ENV`.

## Convenções

- Scripts de ETL idempotentes: usar `MERGE` (nunca `CREATE` cego) para nós e relações,
  de modo que rodar duas vezes não duplique dados.
- Caminhos relativos à raiz do projeto (ex.: `Path(__file__).resolve().parents[2]`),
  nunca caminhos absolutos da máquina.
- Dados (`data/`), `neo4j/` e `.env` ficam fora do Git (ver `.gitignore`).
- Credenciais sempre via `.env` com python-dotenv.
