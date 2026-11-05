# Arquitetura: da proposta ao código

## 1. Preparação do conhecimento (offline)

| Etapa | Implementação | Situação |
|---|---|---|
| 1.1 Seleção do repositório | google/guava | Feita (critérios a documentar) |
| 1.2 Mineração | SQLite extraído da API do GitHub | Parcial: coleta de issues/PRs abortada; repositório Git não minerado (planejado para depois) |
| 1.3 Normalização e relações | [src/etl/carregar_grafo.py](../src/etl/carregar_grafo.py), decisões em [modelo_grafo.md](modelo_grafo.md) | Feita |
| 1.4 Construção do grafo | Neo4j 5.26 + GDS | Feita |

## 2. Atendimento à solicitação do newcomer (online)

Fluxo fixo, não um agente livre: as etapas são chamadas sempre na mesma ordem,
o que torna o resultado reprodutível e cada etapa avaliável separadamente.

| Etapa | Implementação | Situação |
|---|---|---|
| 2.1 Seleção da issue | `uv run kg-onboarding guia <numero>` | Feita (linha de comando) |
| 2.2 Interpretação | `guia.interpretar`: LLM → objetivo, tipo, termos de busca em inglês, código citado (JSON validado por esquema) | Feita (Ollama). Sem LLM: `termos_heuristicos` |
| 2.3 Enriquecimento | [src/kg_onboarding/guia.py](../src/kg_onboarding/guia.py) | Feita: itens semelhantes (busca textual), pessoas, código citado, processo |
| 2.4 Consolidação | `guia.coletar_contexto` | Feita: ranqueamento, fusão de listas de pessoas, cortes |
| 2.5 Síntese | `guia.gerar_guia`: LLM → guia em Markdown com seis seções fixas | Feita (Ollama). Sem LLM: `formatar_markdown` mostra o contexto |

### O que cada seção do contexto contém

- **Itens semelhantes:** issues e PRs pela busca textual (índice `texto_issues_prs`),
  com o estado, o motivo de fechamento e os commits que os resolveram.
- **Pessoas:** fusão (*reciprocal rank fusion*) de duas listas: quem participou de
  itens semelhantes ou do mesmo pacote, e os mantenedores ativos (quem mais fecha
  issues/PRs). Cada pessoa vem com o motivo da sugestão.
- **Código:** classes e métodos citados no texto (`BloomFilter`, `Lists.newArrayList`)
  e arquivos discutidos em revisões de commits. **Limitação:** sem a mineração do
  Git não há ligação commit → arquivo, então "arquivos envolvidos" é fraco por ora.
- **Processo:** aceitação e tempo até a decisão por vínculo do autor (estatística global).

Labels `status=*` são removidas da issue para não revelar o desfecho.

## LLM local (Ollama)

- Modelo `qwen2.5:3b` (3 bilhões de parâmetros, ~2 GB), container `ollama-mestrado`,
  só CPU (16 threads, sem GPU). Temperatura 0 e semente fixa para reprodutibilidade.
- Velocidade medida: ~95 tokens/s lendo o prompt e ~20 tokens/s gerando. Um guia leva
  ~1 min (interpretação 6–13 s, síntese ~50 s com ~1 mil tokens de contexto).
- `LLM_NUM_CTX` explícito (8192): o padrão do Ollama é menor e corta o prompt sem aviso;
  `llm.py` avisa se a janela for atingida.

Observações qualitativas (issue #2098):
- A interpretação é boa: gerou termos que não estão no título (`BloomFilter`,
  `false positive`).
- Na primeira versão, a síntese inventou data e estado da issue e distorceu o processo.
  Corrigido incluindo data/estado no contexto, escrevendo o processo como fatos
  diretos e descrevendo o conteúdo de cada seção no prompt.
- Restam limitações de um modelo pequeno: comenta pouco o desfecho dos itens
  semelhantes e "Onde investigar" fica raso (sem mineração, não há arquivos).

## Avaliação por replay histórico

[avaliacao/replay.py](../avaliacao/replay.py) monta o contexto de cada issue só com o
que existia antes da sua criação e compara com o que aconteceu depois. Mede a
recuperação, que é o que alimenta a síntese (a qualidade do texto final ainda não é
avaliada). Por padrão não usa LLM; com `--llm`, os termos de busca vêm da interpretação.

Resultados (2026-11-05, 459 issues com gabarito, recuperação sem LLM):

| Métrica | Guia | Baseline (só mantenedores ativos) |
|---|---|---|
| Itens relacionados, hit@8 (n=112) | 0,616 | — |
| Itens relacionados, recall@8 | 0,401 | — |
| Pessoas, hit@3 (n=381) | **0,478** | 0,438 |
| Pessoas, hit@6 | 0,630 | 0,640 |
| Pessoas, recall@6 | 0,533 | 0,535 |

Observações:
- Só com a participação em itens da área, o guia perdia para o baseline (hit@3 0,375):
  no período coberto, poucos mantenedores respondem quase tudo. A fusão das duas
  listas resolveu isso. Pesos 2:1 a favor dos mantenedores deram hit@3 0,488, mas
  foram escolhidos olhando estes dados; para relatar, separar treino e teste.
- O gabarito de pessoas mede "quem vai interagir na issue", dominado pela triagem.
  "Quem pode orientar tecnicamente" exigiria outro gabarito (ex.: autor da correção),
  que depende das ligações commit → issue, hoje escassas.
- Os números refletem os dados atuais (issues de 2014–2017). Repetir após completar
  a extração e a mineração.
- **1.152 das 1.446 issues (80 %) foram criadas em 31/10/2014**, data da migração do
  Google Code. No replay, elas só "enxergam" issues migradas minutos antes, não o
  histórico real. Por isso `--limite` usa amostra espaçada, não os primeiros números.

### Interpretação pelo LLM vs. heurística (amostra de 60 issues)

Mesma amostra uniforme (`--limite 60`), `qwen2.5:3b`, 2026-11-05:

| Métrica | Termos heurísticos | Termos do LLM |
|---|---|---|
| Itens relacionados, hit@8 (n=15) | 0,467 | **0,600** |
| Itens relacionados, recall@8 (n=15) | 0,300 | **0,400** |
| Pessoas, hit@6 (n=48) | 0,625 | **0,646** |

O LLM acertou 2 issues a mais e não perdeu nenhuma (ex.: #2000, ao gerar
`InternetDomainName.topPrivateDomain` a partir do corpo). Amostra pequena: indício,
não conclusão. Para confirmar, rodar `--llm` em todas as issues (~1 h na CPU).
