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
- Não alterar a versão da imagem (`neo4j:5.26-community`) sem combinar antes.

## Python (uv)

- Python 3.12 (fixado em `.python-version`); dependências em `pyproject.toml`.
- Rodar scripts: `uv run src/etl/<script>.py`
- Adicionar pacote: `uv add <pacote>` (no zsh, use aspas em extras: `uv add "mcp[cli]"`)
- Teste do ambiente: `uv run src/etl/teste_conexao.py`
- Carga do grafo: `uv run src/etl/carregar_grafo.py [etapas...] [--limpar]`
  (modelo e decisões em `docs/modelo_grafo.md`; utilitários em `src/etl/comum.py`)
- Se houver um `VIRTUAL_ENV` de outro projeto ativo, o uv avisa e o ignora; pode-se rodar `unset VIRTUAL_ENV`.

## Convenções

- Scripts de ETL idempotentes: usar `MERGE` (nunca `CREATE` cego) para nós e relações,
  de modo que rodar duas vezes não duplique dados.
- Caminhos relativos à raiz do projeto (ex.: `Path(__file__).resolve().parents[2]`),
  nunca caminhos absolutos da máquina.
- Dados (`data/`), `neo4j/` e `.env` ficam fora do Git (ver `.gitignore`).
- Credenciais sempre via `.env` com python-dotenv.
