# Perguntas de newcomers e ferramentas do agente

As perguntas estão agrupadas pelas barreiras mais citadas na literatura sobre
newcomers em OSS (ex.: Steinmacher et al., 2015: encontrar uma tarefa, encontrar
um mentor, entender o processo, entender o código e o histórico). Cada grupo
mapeia para ferramentas em [src/kg_onboarding/ferramentas.py](../src/kg_onboarding/ferramentas.py).
O gabarito de avaliação está em [avaliacao/perguntas.jsonl](../avaliacao/perguntas.jsonl).

| Barreira | Perguntas típicas | Ferramenta |
|---|---|---|
| **Escolher onde contribuir** | Quais áreas existem? Qual é a mais ativa? | `listar_pacotes` |
| | O que está em andamento agora? Alguém já está trabalhando nisso? | `prs_abertos` |
| | Isso já foi discutido/reportado antes? | `buscar_discussoes` |
| **Encontrar pessoas** | Com quem falo sobre o pacote X? Quem pode revisar meu PR? | `especialistas_do_pacote` |
| | Quem decide o que entra no projeto? | `mantenedores` |
| | Quem é fulano e no que trabalha? | `perfil_contribuidor` |
| **Entender o histórico** | Do que trata a issue/PR N? Como foi resolvida? | `detalhes` |
| | O que foi discutido na issue N? Por que foi recusada? | `linha_do_tempo` |
| **Entender o processo** | Meu PR tem chance de ser aceito? Quanto tempo demora? Como o PR é integrado? | `processo_de_contribuicao` |
| **Outras** | Perguntas não previstas | `consultar_cypher` (reserva, somente leitura) |

## Como experimentar

```bash
uv run kg-onboarding                                       # lista as ferramentas
uv run kg-onboarding especialistas_do_pacote pacote=cache limite=5
uv run kg-onboarding buscar_discussoes texto="bloom filter"
uv run kg-onboarding detalhes numero=1947
```

## Achados que o agente deve saber explicar

- **Integração via Google:** muitos PRs aceitos aparecem como *closed* sem *merge*. A
  mudança é importada para o repositório interno do Google e volta por um commit
  (sincronizado pelo `copybara-service[bot]`) que fecha o PR. Por isso "aceito" =
  merged OU fechado por commit.
- **Primeira contribuição é difícil:** PRs de autores sem vínculo prévio
  (`author_association = NONE`) tiveram aceitação muito menor do que os de
  `CONTRIBUTOR`/`MEMBER`. Confirme quando os dados estiverem completos (ver abaixo).
- **Labels renomeadas:** a lista `labels` de issues e PRs traz o nome atual
  (`package=io`), mas eventos `labeled` antigos da timeline guardam o nome da época
  (`package: io`). Filtre por pacote usando `labels`, não os eventos.

## Limitação atual dos dados

A extração de **issues** e **pull requests** foi abortada (`__checkpoints` com status
`aborted`). Como a coleta seguia a ordem crescente de atualização, o banco contém
só os itens atualizados há mais tempo: as 1.446 issues são todas fechadas (2014–2017)
e os PRs vão até 2022. Commits, releases, tags e workflows estão completos.
Consequências:

- Não há issues abertas, então ainda não dá para sugerir "por onde começar" com
  tarefas atuais.
- As estatísticas de processo e os especialistas refletem o período coberto, não o
  estado atual do projeto.

As ferramentas não dependem disso: com uma extração completa, basta recarregar o
grafo (`uv run src/etl/carregar_grafo.py --limpar`).
