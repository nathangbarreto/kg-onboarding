# kg-onboarding

Grafo de conhecimento do repositório [google/guava](https://github.com/google/guava)
construído para apoiar *newcomers* (novos contribuidores) em projetos open source,
por meio de **guias contextuais** produzidos por um **fluxo orquestrado** que combina o
grafo e um LLM. Prática exploratória de um projeto de mestrado (UFMS).

## Objetivo

Quem chega a um projeto open source precisa responder perguntas que dependem de
**relações**, e não só de texto:

- Quem costuma discutir ou revisar mudanças em determinado pacote?
- Qual commit ou PR resolveu esta issue, e quem o escreveu?
- Quem faz o merge de PRs e como o CI se comporta em cada commit?

Este projeto transforma os dados extraídos do GitHub (issues, PRs, commits, eventos,
CI, releases) num grafo de conhecimento no Neo4j. Um fluxo orquestrado consulta esse
grafo e, com um LLM, produz o guia contextual de uma issue.

## Como funciona

```
data/raw/guava.sqlite ──► src/etl/carregar_grafo.py ──► Neo4j ──► fluxo orquestrado ──► guia contextual
   (dados do GitHub)          (ETL idempotente)          (grafo)    (consultas + LLM)       (kgo <issue>)
```

1. **Origem:** um banco SQLite com dados do google/guava extraídos da API do GitHub
   (1.446 issues, 1.405 PRs, 7.503 commits, 27.526 eventos de timeline, 1.531 usuários etc.).
2. **ETL:** scripts Python leem o SQLite, decodificam as colunas JSON e gravam nós e
   relações no Neo4j com `MERGE`. Rodar de novo atualiza os dados sem duplicar.
3. **Grafo de conhecimento:** Neo4j 5.26 (com o plugin Graph Data Science instalado,
   ainda não usado), 43.826 nós e 77.097 relações.
4. **Fluxo orquestrado:** etapas fixas que vão do número da issue ao guia contextual
   (ver [Guia contextual de uma issue](#guia-contextual-de-uma-issue-fluxo-orquestrado)).

### Modelo do grafo (resumo)

```mermaid
graph LR
  User -->|OPENED / ASSIGNED_TO| Issue
  User -->|OPENED / MERGED / REVIEWED| PullRequest
  User -->|AUTHORED / COMMITTED| Commit
  TimelineEvent -->|ON| Issue
  TimelineEvent -->|ON| PullRequest
  Issue -->|MENTIONS| Issue
  Issue -->|IN_MILESTONE| Milestone
  PullRequest -->|HAS_COMMIT / MERGED_AS| Commit
  Commit -->|CLOSES / REFERENCES| Issue
  CommitComment -->|ON_COMMIT| Commit
  CommitComment -->|ON_FILE| File
  Workflow -->|RAN_ON| Commit
  Tag -->|POINTS_TO| Commit
  Release -->|FROM_TAG| Tag
```

O modelo completo, a origem de cada relação e as decisões de modelagem estão em
[docs/modelo_grafo.md](docs/modelo_grafo.md).

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala o Python 3.12 e as dependências)
- Docker com Docker Compose (Neo4j e Ollama rodam em containers)
- O arquivo `guava.sqlite` (não versionado; obtido separadamente)

## Instalação

```bash
git clone <url-deste-repositório> kg-onboarding && cd kg-onboarding
uv sync                                     # cria .venv com Python 3.12 e dependências

mkdir -p data/raw && cp /caminho/para/guava.sqlite data/raw/
```

Crie um `.env` na raiz a partir de `.env.example` (`cp .env.example .env`; ele está no `.gitignore`):

```dotenv
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<uma senha forte>
```

Suba o Neo4j e confira o ambiente:

```bash
docker compose up -d                        # aguarde "Started." em: docker logs neo4j-mestrado
uv run src/etl/teste_conexao.py
```

Saída esperada:

```
[OK] .env carregado
[OK] SQLite: 1446 linhas em issues (esperado 1446)
[OK] Neo4j: conectado em bolt://localhost:7687
Tudo OK.
```

## Uso

### Carregar o grafo

```bash
uv run src/etl/carregar_grafo.py               # todas as etapas (~16 s)
uv run src/etl/carregar_grafo.py issues refs   # apenas algumas etapas
uv run src/etl/carregar_grafo.py --limpar      # apaga o grafo e recarrega
```

Etapas disponíveis: `users`, `milestones`, `issues`, `commits`, `pull_requests`,
`timeline`, `commit_comments`, `workflows`, `refs`, `discussions`.
Ao final, o script mostra a contagem de nós e relações por tipo.

### Explorar o grafo

- **Neo4j Browser:** http://localhost:7474 (usuário `neo4j`, senha do `.env`)
- **Terminal:**
  ```bash
  docker exec -it neo4j-mestrado cypher-shell -u neo4j -p "$(grep ^NEO4J_PASSWORD .env | cut -d= -f2-)"
  ```
- **Python / notebooks:** `uv run jupyter lab` (salve os notebooks em `notebooks/`)

### Exemplos de consulta

Pessoas que mais discutem issues do pacote `cache` (candidatas a mentoras):

```cypher
MATCH (i:Issue)<-[:ON]-(:TimelineEvent {type: 'commented'})<-[:PERFORMED]-(u:User)
WHERE 'package=cache' IN i.labels AND u.login <> 'gissuebot'
RETURN u.login, count(DISTINCT i) AS issues
ORDER BY issues DESC LIMIT 5;
```

```
ben-manes  9
lowasser   8
kevinb9n   6
...
```

Commits que fecharam issues, com seus autores:

```cypher
MATCH (a:User)-[:AUTHORED]->(c:Commit)-[:CLOSES]->(i:Issue)
RETURN i.number, i.title, a.login LIMIT 10;
```

Quem faz merge de PRs:

```cypher
MATCH (u:User)-[:MERGED]->(:PullRequest)
RETURN u.login, count(*) AS merges ORDER BY merges DESC LIMIT 5;
```

Linha do tempo de uma issue:

```cypher
MATCH (i:Issue {number: 3})<-[:ON]-(e:TimelineEvent)
OPTIONAL MATCH (u:User)-[:PERFORMED]->(e)
RETURN e.position, e.type, u.login, left(e.body, 80)
ORDER BY e.position;
```

### Catálogo de consultas

O catálogo de consultas está em `src/kg_onboarding/ferramentas.py`: nove consultas
curadas, cada uma ligada a uma dúvida típica de newcomer
([docs/perguntas_newcomer.md](docs/perguntas_newcomer.md)), e uma consulta livre em
Cypher restrita a leitura. Cada consulta tem nome, descrição e parâmetros em JSON
Schema. Todas rodam sem permissão de escrita (roteamento de leitura e barreira contra
escrita) e podem ser chamadas pelo terminal:

```bash
uv run kg-onboarding                                        # lista o catálogo de consultas
uv run kg-onboarding especialistas_do_pacote pacote=cache limite=3
```

```json
[
  {"login": "ben-manes", "comentarios": 12, "prs": 1, "commits": 2, "pontuacao": 20, ...},
  {"login": "lowasser",  "comentarios": 8,  "revisoes": 1, "pontuacao": 11, ...},
  {"login": "kluever",   "comentarios": 5,  "revisoes": 1, "pontuacao": 8, ...}
]
```

Testes de integração (33; exigem o Neo4j com o grafo carregado): gabarito em
`avaliacao/perguntas.jsonl` (cada pergunta de newcomer com a barreira, a consulta e os
argumentos esperados), esquemas das consultas, barreira contra escrita, guias
contextuais pré-gerados e etapas do fluxo orquestrado e da análise por snapshot:

```bash
uv run pytest
```

### Teste com newcomers (MVP)

Para testar sem que o participante precise de Docker, banco ou chave de API, o pacote
leva guias contextuais pré-gerados de algumas issues (`src/kg_onboarding/dados/guias.json`).
Sem Neo4j acessível, o comando `kgo` entra em modo offline e usa esses guias.

```bash
uv run kg-onboarding exportar 669 2098 ...   # gera/atualiza guias contextuais (grafo + LLM)
uv build --wheel                             # cria dist/kg_onboarding-<versão>-py3-none-any.whl
```

Envie o `.whl` e as instruções de [docs/teste_mvp.md](docs/teste_mvp.md). O participante
instala com `uv tool install <arquivo>.whl` e usa `kgo <issue>`. No clone, `uv run kgo
<issue>` roda ao vivo; `--offline` força os guias contextuais pré-gerados.

### Guia contextual de uma issue (fluxo orquestrado)

O atendimento ao newcomer ([docs/arquitetura.md](docs/arquitetura.md)) é um fluxo
orquestrado: as etapas rodam sempre na mesma ordem, a partir do número da issue.

| Etapa | Implementação |
|---|---|
| Interpretar a issue | `guia.interpretar`: LLM com saída validada por esquema (objetivo, tipo, termos de busca, elementos de código); sem LLM, `guia.termos_heuristicos` |
| Enriquecer contexto da issue | `guia.coletar_contexto`: quatro consultas, em sequência |
| ↳ Buscar itens semelhantes | `guia._semelhantes` (índice de texto completo; estado, motivo do fechamento, commits que resolveram) |
| ↳ Identificar pessoas relacionadas | `guia._pessoas` (quem participou dos itens semelhantes ou do mesmo pacote) |
| ↳ Localizar elementos de código | `guia.elementos_de_codigo` (classes e métodos citados) e arquivos discutidos em revisões |
| ↳ Recuperar processo de contribuição | `ferramentas.processo_de_contribuicao` (aceitação e tempo até a decisão) |
| Consolidar e priorizar o contexto | `guia.fundir_pessoas` (*reciprocal rank fusion* com os mantenedores ativos, com o motivo de cada sugestão) e cortes |
| Sintetizar guia contextual | `guia.gerar_guia`: contexto de até ~8 mil caracteres; o LLM redige em português, com seis seções fixas, usando só esse contexto |

O LLM pode ser o Claude, pela API da Anthropic, ou um modelo local no
[Ollama](https://ollama.com). Todas as variáveis estão em [.env.example](.env.example).

**Claude (padrão quando há chave).** Coloque a chave no `.env`; o modelo padrão é o
`claude-haiku-5-5`, o mais barato (US$ 0,10 / 0,50 por milhão de tokens de entrada/saída;
um guia contextual custa cerca de US$ 0,001 e o custo aparece no rodapé):

```dotenv
ANTHROPIC_API_KEY=sk-ant-...
# LLM_MODELO=claude-sonnet-5-5   # opcional: outro modelo do Claude
```

Os modelos atuais do Claude não aceitam temperatura nem semente: a reprodutibilidade
vem do esquema JSON na interpretação e do registro de cada resposta.

**Ollama (local, sem custo).** Sem `ANTHROPIC_API_KEY` (ou com `LLM_PROVEDOR=ollama`).
Na primeira vez, baixe o modelo (~2 GB):

```bash
docker compose --profile ollama up -d
docker exec ollama-mestrado ollama pull qwen2.5:3b
```

```bash
uv run kg-onboarding guia 2098              # guia contextual (segundos no Claude; ~1 min no Ollama/CPU)
uv run kg-onboarding guia 2098 --sem-llm    # só o contexto do grafo (instantâneo)
uv run kg-onboarding guia 2098 --snapshot   # análise por snapshot: só o que existia na criação da issue
uv run kg-onboarding guia 2098 --json       # saída estruturada completa
```

### Análise por snapshot (exploratória)

```bash
uv run avaliacao/replay.py                  # ~20 s; resultados em data/avaliacao/
uv run avaliacao/replay.py --limite 60 --llm  # termos de busca vindos do LLM (~10 min)
```

Reconstrói o estado do grafo na data de criação de cada issue, monta o contexto só
com as informações daquele instante, sem informação futura, e o compara com os itens
que vieram a ser referenciados e com as pessoas que participaram da resolução. É uma
análise exploratória, ainda a amadurecer. Resultados em
[docs/arquitetura.md](docs/arquitetura.md#análise-por-snapshot-exploratória).

### Parar o Neo4j

```bash
docker compose down        # os dados ficam em neo4j/data
```

## Estrutura

```
├── data/                  # não versionado
│   ├── raw/guava.sqlite   # banco de origem
│   └── repos/             # clones de repositórios (mineração com PyDriller)
├── docs/                  # arquitetura, modelo do grafo, perguntas de newcomer, teste MVP
├── neo4j/                 # volume do Neo4j (não versionado)
├── notebooks/             # análises exploratórias
├── src/
│   ├── etl/
│   │   ├── comum.py           # conexões e utilitários
│   │   ├── carregar_grafo.py  # SQLite -> Neo4j
│   │   └── teste_conexao.py   # verificação do ambiente
│   └── kg_onboarding/
│       ├── __init__.py        # CLI (kg-onboarding e kgo)
│       ├── config.py          # configuração (.env) e caminhos
│       ├── grafo.py           # acesso somente leitura ao Neo4j
│       ├── ferramentas.py     # catálogo de consultas
│       ├── guia.py            # fluxo orquestrado do guia contextual
│       ├── llm.py             # LLM com provedores intercambiáveis (Claude Haiku 5.5 / Ollama) e cache
│       └── dados/guias.json   # guias contextuais pré-gerados (modo offline)
├── avaliacao/
│   ├── perguntas.jsonl    # gabarito: pergunta de newcomer, barreira, consulta e argumentos
│   └── replay.py          # análise por snapshot (exploratória)
├── tests/                 # testes de integração
├── docker-compose.yml     # Neo4j 5.26 Community + GDS e Ollama (LLM local)
├── ollama/                # modelos do Ollama (não versionado)
└── pyproject.toml
```

## Convenções

- Scripts de ETL **idempotentes**: sempre `MERGE`, nunca `CREATE` sem chave.
- Caminhos relativos à raiz do projeto.
- Credenciais apenas no `.env`; `data/`, `neo4j/` e `.env` ficam fora do Git.
- Dependências gerenciadas com `uv add <pacote>`.

## Limitações conhecidas

- **Extração incompleta de issues e PRs:** a coleta foi abortada (ver `__checkpoints`
  no SQLite). O banco tem só os itens atualizados há mais tempo: todas as issues estão
  fechadas (2014–2017) e os PRs vão até 2022. Detalhes em
  [docs/perguntas_newcomer.md](docs/perguntas_newcomer.md#limitação-atual-dos-dados).

- Não há ligação direta entre releases e PRs nos dados. Ela depende do histórico git
  (`git tag --contains`), ainda não minerado.
- Cerca de 3.750 commits existem apenas como referência (só o `sha`): têm
  `in_dataset = false`; os 7.503 completos têm `in_dataset = true`.
- Arquivos (`File`) vêm só de comentários em commits. A relação commit → arquivo
  virá da mineração com PyDriller.
- Contas automatizadas (`gissuebot`, `copybara-service[bot]`) distorcem rankings de
  pessoas e devem ser filtradas.

## Próximos passos

- [ ] Minerar o histórico git com PyDriller (`Commit -[:MODIFIES]-> File`, releases → PRs)
- [ ] Avaliar nós `Label`/`Package` a partir das labels das issues
- [ ] Aplicar algoritmos do GDS (centralidade de contribuidores, comunidades de arquivos)
- [x] Catálogo de consultas e gabarito inicial de avaliação
- [ ] Completar a extração de issues e PRs
- [x] Fluxo orquestrado do guia contextual, com análise por snapshot (exploratória)
- [x] Interpretação e síntese com LLM (Claude Haiku 5.5 ou Qwen 2.5:3b via Ollama), com cache
- [ ] Modo agente (o LLM escolhe as consultas) e servidor MCP expondo o catálogo
- [ ] Comparação sistemática entre LLMs e avaliação da qualidade do guia contextual

## Autor

Nathan Gabriel Barreto Santos — mestrado, UFMS.
