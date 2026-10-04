<div align="center">

<img src="assets/banner.svg" alt="devin-metrics" width="100%"/>

</div>

# devin-metrics

> **Projeto comunitário não oficial.** Sem afiliação, endosso ou patrocínio da
> Cognition AI. "Devin" é marca registada da Cognition AI.

**[English](README.md)** · Português (BR)

Métricas locais do teu uso do Devin: sessões por dia/semana, rollups por
projeto e modelo, picos de contexto, sessões mais longas, distribuição de
tool-calls — zero telemetria, saída em JSON + markdown.

## O problema

As sessões do Devin acumulam atividade real — crescimento de contexto,
tempo de modelo, tool-calls — mas não há como responder "que projeto comeu
a minha semana?" ou "quão grandes ficaram as minhas sessões?". Os dados já
existem em disco em `sessions.db` e `acp-messages/*.db`; nada os lê.
`devin-metrics` é o lado de leitura que faltava.

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
  diretamente: as métricas vêm de dados do protocolo (`sessions.db`,
  `acp-messages`), não de texto raspado.
- **Sem Devin:** remove o Devin e não há nada para medir — sem store, sem
  métricas.
- **Uma frase:** lê as bases de dados do próprio Devin e diz o que as tuas
  sessões fizeram — localmente, sem enviar nada.

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
- **Custo não persiste localmente — verificado.** Uma instalação real
  (2026-10) confirma que os payloads acp e o `tool_call_state` **não**
  carregam campos de custo/tokens; o custo por turno existe apenas no meta
  da sessão ACP ao vivo e nunca vai para disco. `cost_usd` mostra `-` em
  dados reais. O único sinal de tokens que *persiste* —
  `num_tokens_preceding` em `message_nodes.metadata` — aparece por sessão
  como `context_tokens` (pico de contexto). Detalhes: `docs/SCHEMA.md`.
- **Schema com gate.** Versões de `sessions.db` fora de v15–v17 são
  recusadas com erro claro (via o detector do `devin-internals-spec`).
- Verificado com drift-check — `devin-inspect contract` (do
  devin-internals-spec) valida a instalação contra todas as fronteiras de
  contrato conhecidas.

## Desenvolvimento

```bash
pip install -e ".[dev]"
python -m pytest
```

## Quando usar

- Você quer saber quanto custa o seu uso do Devin: totais por projeto, modelo ou dia, mais as sessões mais longas e o mix de tool calls.
- Você precisa de um feed JSON scriptável de estatísticas de uso (`--json` em todos os comandos).
- Você quer um dashboard HTML standalone de atividade (`devin-metrics dashboard` ou o alias `devin-dashboard`).
- Telemetria é um não absoluto — tudo é calculado e guardado localmente.

## Quando NÃO usar

- Você precisa de pesquisar conteúdo de mensagens — use `devin-search`; ou queries de relações entre sessões/ficheiros/ferramentas — use `devin-graph`.
- Você precisa de monitorização de sessões ao vivo em tempo real — use `devin-office`.
- A máquina não tem instalação Devin CLI/Desktop — não há nada para medir.

## FAQ

**O que é o devin-metrics?** Um CLI local que lê as próprias bases de sessões do Devin e reporta métricas de uso: sessões por dia/semana, totais de custo e tokens por projeto e modelo, sessões mais longas e mix de tool calls. Também traz um alias `devin-dashboard` que escreve um dashboard HTML standalone.

**Como o devin-metrics obtém dados de custo?** Resposta honesta: na maior parte não obtém — verificado numa instalação real, os stores locais do Devin **não** persistem campos de custo/tokens (o custo só existe no meta da sessão ACP ao vivo). O que ele mede: sessões, mensagens, tool calls, durações, rollups por projeto/modelo e `context_tokens` (pico de `num_tokens_preceding` — o único sinal de tokens que persiste). O adaptador `extract_usage()` fica pronto para uma mudança futura de schema.

**O devin-metrics envia dados para algum lado?** Não. Todas as métricas são calculadas localmente e escritas numa base de dados local. Não há chamadas de rede nem telemetria; os stores do Devin são abertos `mode=ro` e nunca escritos.

## Licença

MIT — vê [LICENSE](LICENSE).
