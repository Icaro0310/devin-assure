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

Requer Python ≥ 3.10 e `pipx`. **Windows (PowerShell):** instale `pipx` com `py -m pip install --user pipx`, execute `py -m pipx ensurepath` e reabra o terminal. **Linux (Debian/Ubuntu):** execute `sudo apt install pipx python3-venv` e `pipx ensurepath`; reabra o terminal. Noutras distribuições Linux, instale `pipx` pelo gestor de pacotes.

Este pacote ainda não está no PyPI; instale a versão pública do GitHub:

```bash
pipx install "devin-metrics @ git+https://github.com/Icaro0310/devin-metrics.git"
```

## Uso

```bash
devin-metrics summary                  # números principais + top-5
devin-metrics projects                 # tabela custo/sessões por projeto
devin-metrics daily --days 14          # atividade ao longo do tempo
devin-metrics dashboard --out usage.html

devin-dashboard build --out usage.html # alias do executável dashboard
devin-dashboard data --json            # dados do dashboard em JSON
devin-metrics summary --json           # JSON puro para scripts
```

`devin-dashboard` é um alias instalado pelo mesmo pacote. `build` escreve um
HTML autónomo; `data` imprime o payload normalizado das métricas.

Por omissão, `sessions.db` é lida da raiz de dados da plataforma
(`%APPDATA%/devin` no Windows, `$XDG_DATA_HOME/devin` no Linux, normalmente
`~/.local/share/devin`). Logs ACP são lidos da raiz de configuração UI separada
(`%APPDATA%/Devin/User` no Windows, `$XDG_CONFIG_HOME/Devin/User` no Linux).
Sobrepõe com `--data-dir`, `--sessions-db` ou `--acp-dir`.

```bash
devin-metrics summary --sessions-db caminho/sessions.db --acp-dir caminho/acp-messages
```

Um `acp-messages` ausente degrada graciosamente: tudo exceto colunas de
custo/tokens continua a funcionar, e `cost_usd` mostra `-` (desconhecido ≠
zero).

## Funciona só com o Devin (modo Devin-only)

Todas as métricas são calculadas localmente a partir das stores do próprio
Devin e gravadas numa base de dados local — zero telemetria, zero chamadas de
rede. O alias `devin-dashboard` incluído neste pacote (que absorveu o antigo
dashboard standalone) também renderiza inteiramente na tua máquina.

## Suporte de plataformas

Testado em **Windows e Linux** (o CI corre em `windows-latest` +
`ubuntu-latest`). A base CLI é auto-detetada de `%APPDATA%/devin/cli/sessions.db`
no Windows e `$XDG_DATA_HOME/devin/cli/sessions.db` no Linux (por omissão
`~/.local/share/devin/cli/sessions.db`). Os logs ACP são lidos de
`$XDG_CONFIG_HOME/Devin/User/acp-messages` (por omissão
`~/.config/Devin/User/acp-messages`). Estruturas antigas `~/.config/devin`
também são verificadas. Sobrepõe com `--sessions-db` ou `--acp-dir`.

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
