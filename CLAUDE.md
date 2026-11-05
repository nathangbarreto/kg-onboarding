# kg-onboarding

Projeto de mestrado: construir um grafo de conhecimento (Neo4j) a partir de um banco
SQLite com dados extraídos do repositório GitHub `google/guava`. O grafo será usado por
um agente de IA para apoiar newcomers em contribuições open source.

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
.env                    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD (não versionado)
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
- Ferramentas do agente: `src/kg_onboarding/ferramentas.py` (registrar com `@ferramenta`,
  sempre via `grafo.consultar`, que roda em modo leitura); CLI `uv run kg-onboarding`
- Testes: `uv run pytest` (integração; precisa do Neo4j com o grafo carregado). Ao criar
  ferramenta, acrescente perguntas em `avaliacao/perguntas.jsonl`.
- Dados de issues/PRs estão incompletos (extração abortada); ver `docs/perguntas_newcomer.md`.
- Guia de uma issue (arquitetura online, fluxo fixo): `src/kg_onboarding/guia.py`;
  `uv run kg-onboarding guia <n> [--sem-llm] [--replay] [--json]`. Mapa e resultados: `docs/arquitetura.md`
- LLM: Ollama local (escolha do usuário), container `ollama-mestrado` no docker-compose,
  modelo `qwen2.5:3b`; acesso só via `src/kg_onboarding/llm.py` (temperatura 0, seed fixa,
  `num_ctx` explícito). CPU apenas: manter o contexto da síntese compacto.
- Avaliação por replay: `uv run avaliacao/replay.py` (resultados em `data/avaliacao/`).
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
