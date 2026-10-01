# devin-metrics

> **Projeto comunitário não oficial.** Sem afiliação, endosso ou patrocínio da
> Cognition AI. "Devin" é marca registada da Cognition AI.

**[English](README.md)** · Português (BR)

Métricas locais do teu uso do Devin: sessões por dia/semana, custo e tokens
agregados por projeto e modelo, sessões mais longas, distribuição de
tool-calls — zero telemetria, saída em JSON + markdown.

## O problema

As sessões do Devin acumulam custo real — tokens, tempo de modelo,
tool-calls — mas não há como responder "quanto gastei esta semana?" ou
"que projeto consome o meu orçamento?". Os dados já existem em disco em
`sessions.db` e `acp-messages/*.db`; nada os lê. `devin-metrics` é o lado
de leitura que faltava.

## Trabalho anterior (prior art)

Existem trackers de uso para outros agentes — p.ex. `ccusage` para Claude
Code lê transcripts de `~/.claude` e reporta rollups de custo/tokens. Este
projeto adapta a mesma ideia; não reinventa a roda. O que difere é a
*fonte*: os stores do Devin são privados, com schema versionado (17
migrações) e sem documentação — por isso a leitura é delegada ao
[`devin-internals-spec`](https://github.com/Icaro0310/devin-internals-spec),
dono do parsing + deteção de schema.

## O que o torna Devin-native

- **Lado a lado:** trackers genéricos de tokens não conseguem abrir os
  stores do Devin — o formato não é publicado. Esta ferramenta lê-os
  diretamente: o custo vem de dados do protocolo (`acp-messages`), não de
  texto raspado.
- **Sem Devin:** remove o Devin e não há nada para medir — sem store, sem
  métricas.
- **Uma frase:** lê as bases de dados do próprio Devin e diz quanto custam
  as tuas sessões — localmente, sem enviar nada.

O `working_directory` de cada sessão dá atribuição por projeto de graça.

## Instalação

```bash
pipx install devin-metrics
```

Até estar no PyPI, instala do repositório:

```bash
pipx install git+https://github.com/Icaro0310/devin-metrics.git
```

## Uso

```bash
devin-metrics summary                  # números principais + top-5
devin-metrics projects                 # tabela custo/sessões por projeto
devin-metrics daily --days 14          # atividade ao longo do tempo
devin-metrics summary --json           # JSON puro para scripts
```

Por omissão, os stores são localizados no data dir da plataforma
(`%APPDATA%/devin` no Windows). Podes sobrepor com `--data-dir`, ou apontar
diretamente para os stores:

```bash
devin-metrics summary --sessions-db caminho/sessions.db --acp-dir caminho/acp-messages
```

Um `acp-messages` ausente degrada graciosamente: tudo exceto colunas de
custo/tokens continua a funcionar, e `cost_usd` mostra `-` (desconhecido ≠
zero).

## Suporte de plataformas

Testado em **Windows e Linux** (o CI corre em `windows-latest` +
`ubuntu-latest`). As stores locais do Devin são auto-detetadas por
plataforma — `%APPDATA%` no Windows, `~/.config/devin/` (XDG) no Linux,
`~/Library/Application Support/devin/` no macOS. Passa um caminho
explícito para override (ver Uso).

## Limitações

- **Read-only, sem rede.** Os stores abrem em `mode=ro`; nada é escrito ou
  enviado.
- **Shape de custo assumido.** O JSON de `messages.payload` carrega campos
  de modelo/custo segundo a nossa leitura — documentado e marcado *não
  verificado* em `docs/SCHEMA.md`; a assunção está isolada em
  `collect.extract_usage()` para que uma correção toque numa só função.
- **Schema com gate.** Versões de `sessions.db` fora de v15–v17 são
  recusadas com erro claro (via o detector do `devin-internals-spec`).
- Verificado apenas contra fixtures sintéticos — uma instalação real pode
  revelar drift de shape (ver STATUS.md → M2).

## Desenvolvimento

```bash
pip install -e ".[dev]"
python -m pytest
```

## Licença

MIT — vê [LICENSE](LICENSE).
