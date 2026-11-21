# Teste do kgo: guia para começar em uma issue do Guava

O `kgo` mostra, para uma issue do projeto [google/guava](https://github.com/google/guava),
um guia para quem quer contribuir pela primeira vez: resumo da issue, discussões
relacionadas, onde investigar no código, com quem falar e como contribuir.

Você recebeu um arquivo `kg_onboarding-0.2.0-py3-none-any.whl`. Não é preciso
conta, chave de API, Docker nem banco de dados.

## 1. Instalar o uv (uma vez)

O [uv](https://docs.astral.sh/uv/) instala o Python e o programa automaticamente.

- **Linux / macOS:**
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Windows (PowerShell):**
  ```powershell
  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

Feche e abra o terminal depois de instalar.

## 2. Instalar o kgo

No terminal, na pasta onde está o arquivo `.whl`:

```bash
uv tool install kg_onboarding-0.2.0-py3-none-any.whl
```

## 3. Usar

```bash
kgo --lista          # issues disponíveis neste teste
kgo 669              # guia da issue #669
kgo 669 --sem-llm    # só os dados do grafo, sem o texto gerado por IA
```

Para remover depois: `uv tool uninstall kg-onboarding`.

## 4. O que observar

Escolha uma ou duas issues da lista, leia o guia e, se quiser, abra a issue no GitHub
(`https://github.com/google/guava/issues/<número>`). Depois, responda:

1. O guia ajudou a entender o que a issue pede e por onde começar?
2. Alguma informação parecia errada, inventada ou inútil?
3. O que faltou para você se sentir pronto para contribuir?

> Os guias foram gerados a partir de um retrato dos dados do Guava (issues de
> 2014–2017) com o modelo Claude Haiku 5.5. São um protótipo de pesquisa de
> mestrado (UFMS).
