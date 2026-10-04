<div align="center">

<img src="assets/banner.svg" alt="devin-evals" width="100%"/>

<a href="https://github.com/Icaro0310/devin-evals/actions/workflows/tests.yml"><img src="https://github.com/Icaro0310/devin-evals/actions/workflows/tests.yml/badge.svg" alt="tests"/></a>


</div>

# devin-evals

> **Projeto comunitário não oficial.** Sem afiliação, endosso ou patrocínio da
> Cognition AI. "Devin" é marca registada da Cognition AI.

**[English](README.md)** · Português (BR)

Um harness de avaliação para trabalho de agentes: defines casos avaliáveis
(sessão + rubrica), fazes replay sobre sessões Devin gravadas e medes a
qualidade ao longo do tempo — para que "o agente está a melhorar?" tenha um
número.

## O problema

Afinas prompts, ficheiros de regras e modelos — e avalias o resultado por
intuição, uma execução anedótica de cada vez. Não há sinal de regressão.
Entretanto, cada sessão Devin já grava, em `sessions.db`, a transcrição
completa e a tabela `tool_call_state`: *que tools correram, com que
argumentos, com que exit codes*. Essa ground truth está parada no teu
disco.

## Trabalho anterior (prior art)

Frameworks genéricas de evals ([OpenAI evals](https://github.com/openai/evals),
[promptfoo](https://github.com/promptfoo/promptfoo),
[Braintrust](https://www.braintrust.dev/)) avaliam *texto de saída* — via de
regra com juiz LLM. Harnesses estilo SWE-bench avaliam repositórios
públicos, não as sessões reais do teu agente. O devin-evals adapta a ideia
de rubricas/graders; não reinventa a roda. O que acrescenta é o corpus:
checks determinísticos sobre a ground truth de tool calls gravada pelo
Devin.

## O que o torna Devin-native

Rubricas como **"tem de chamar `devin_redact` antes de publicar"** tornam-se
factos verificáveis. Os graders leem `tool_call_state` via
[`devin-internals-spec`](https://github.com/Icaro0310/devin-internals-spec),
logo um check afirma "a tool `run_shell` foi invocada com `pytest` nos args
e saiu com 0" — um facto, não um palpite de LLM.

- *Lado a lado:* o promptfoo não consegue afirmar "a tool X foi chamada com
  args contendo Y" — nunca vê a tabela de tool calls do Devin.
- *Sem Devin:* sem `sessions.db`, não há graders de tool calls nem modo
  replay.

## Instalação

Requer Python ≥ 3.10 e `pipx`. **Windows (PowerShell):** instale `pipx` com `py -m pip install --user pipx`, execute `py -m pipx ensurepath` e reabra o terminal. **Linux (Debian/Ubuntu):** execute `sudo apt install pipx python3-venv` e `pipx ensurepath`; reabra o terminal. Noutras distribuições Linux, instale `pipx` pelo gestor de pacotes.

```bash
pipx install "devin-evals @ git+https://github.com/Icaro0310/devin-evals.git"
# a partir do checkout:
pip install -e .
```

Requer Python ≥3.10. Dependências de runtime: só `devin-internals-spec` —
sem rede, sem chamadas LLM.

## Uso

Escreve casos em `evals/*.json`:

```json
{
  "id": "redact-before-publish",
  "description": "O fluxo de relatório tem de se manter higiénico",
  "session_ref": "Refinery session 2026-09-29",
  "rubric": [
    { "grader": "tool_called", "name": "devin_redact" },
    { "grader": "tool_called", "name": "run_shell", "args_substr": "pytest" },
    { "grader": "exit_code", "value": 0, "mode": "all" },
    { "grader": "contains", "text": "all tests pass" },
    { "grader": "no_secrets" }
  ]
}
```

`session_ref` corresponde a um **id ou título** de sessão em `sessions.db`.
Depois:

```powershell
# Windows PowerShell
devin-evals list --evals evals
devin-evals run --evals evals --sessions-db "$env:APPDATA\devin\cli\sessions.db" --out report
```

```bash
# Linux
sessions_db="${XDG_DATA_HOME:-$HOME/.local/share}/devin/cli/sessions.db"
devin-evals list --evals evals
devin-evals run --evals evals --sessions-db "$sessions_db" --out report
```

`run` escreve `report/report.json` + `report/report.md` (PASS/FAIL/
SKIP/ERROR por caso + score agregado; reruns são byte-idênticos) e sai com
0 se tudo passou, 1 em falhas, 2 em erros de uso/IO.

**Experimenta sem um Devin instalado:**

```bash
python -m devin_evals.demo demo.db
devin-evals run --evals evals --sessions-db demo.db
```

A pasta `evals/` incluída traz um caso que passa, um que falha de
propósito e um de ground truth de tool calls.

### Graders

| grader | o que verifica |
|---|---|
| `contains` / `not_contains` | substring literal na transcrição (case-sensitive) |
| `tool_called` | tool `name` chamada ≥`min_calls`, `args_substr` opcional no JSON da call |
| `file_exists` | path no disco — relativo resolve sob o `working_directory` da sessão |
| `exit_code` | exit codes gravados iguais a `value` conforme `mode` (`all`/`any`/`last`) |
| `no_secrets` | zero strings com formato de segredo (regexes vendored do devin-redact) na transcrição + JSON das tools |

## Funciona só com o Devin (modo Devin-only)

O devin-evals pontua sessões gravadas com rubricas determinísticas — sem
chamadas a LLM, sem acesso à rede, nada além dos dados de sessão do próprio
Devin e Python.

## Suporte de plataformas

Python stdlib puro — comportamento idêntico em Windows, Linux e macOS. O
CI corre a suite em `windows-latest` + `ubuntu-latest`; o ficheiro ou
diretório alvo é sempre um argumento explícito, sem paths
específicos de plataforma.


### Rubric packs (EV-4)

Conjuntos reutilizáveis de checks para tipos comuns de sessão. Embutidos:
`bugfix`, `feature`, `refactor` — cada um é uma rubrica de higiene (exit
codes limpos, sem tracebacks, sem segredos) pensada para ser **estendida**
pela `rubric` do próprio caso. Use `"packs": ["bugfix"]` no JSON do caso;
os checks do pack rodam **antes** dos checks locais. Liste com
`devin-evals packs`; sobrescreva ou adicione packs com `--packs-dir <dir>`.

### Corpus golden (EV-3)

`devin-evals corpus` materializa e faz replay de um corpus determinístico
de **sessões sintéticas rotuladas** — nove classes de defeito (D01–D09, o
mesmo catálogo do [devin-dream](https://github.com/Icaro0310/devin-dream)),
cada uma com um veredito conhecido — mais os casos `evals/*.json`
correspondentes, cujas rubricas codificam esses vereditos na forma que os
graders conseguem expressar. É o gate de CI que prova que evals, fixtures
e graders concordam:

```bash
devin-evals corpus generate --out .corpus   # sessions.db + evals/ + corpus.json
devin-evals corpus verify  --corpus .corpus # esperado-vs-real por caso
```

`generate` importa `devin_dream.defects` quando o pacote é importável
(`PYTHONPATH=../devin-dream/src`, ou `--generator dream` para exigi-lo);
senão usa a cópia vendored em `devin_evals._vendored_dream` — ambos
produzem corpora idênticos. `--generator vendored` força a cópia embutida.
Tudo é determinístico dado `--seed` e **somente sintético**: o corpus
nunca deve apontar para um `sessions.db` real.

O corpus também é **versionado** no repo: `corpus/evals/*.json` e
`corpus/corpus.json` são committed, enquanto os `sessions*.db` são sempre
regenerados no lugar (nunca commitados — `*.db` é gitignored).
`tools/regen-corpus.py` reconstrói tudo deterministicamente e serve de
gate de CI contra drift:

```bash
python tools/regen-corpus.py          # reconstrói corpus/ no lugar
python tools/regen-corpus.py --check  # sai 1 se o corpus committed divergir
python tools/regen-corpus.py --verify # reconstrói + replay das expectativas
```

`--check` reusa o `seed`/`generator` gravados em `corpus/corpus.json`
(o corpus committed é pinado em `vendored`, logo o gate é reproduzível
sem um checkout do devin-dream).

Cada caso carrega `expected_status` (o veredito que o caso *deveria*
alcançar: `pass` para o controle limpo D03, `fail` onde um defeito deve
ser detectado, `error` para o canário de drift D06, cujo db v18 é
recusado na abertura). `verify` imprime `MATCH` / `GAP` / `MISMATCH` por
caso e sai com 1 em qualquer mismatch não documentado; `--strict` também
reprova gaps documentados.

**Gaps conhecidos dos graders** (reportados, tolerados por padrão — esta
lista alimenta o roadmap): D05 precisa de um grader de PII/privacidade
(`no_secrets` cobre apenas formatos de segredo); D07 precisa de um grader
que inspecione a *saída* de tool calls por instruções injetadas; D09
precisa de junção de segredo entre payloads (uma chave dividida em duas
saídas de tools escapa de todo padrão de texto único). A granularidade de
veredito também é mais grossa que o catálogo de origem: UNVERIFIED e
PARTIAL do qa-pack colapsam em `fail`, e o "quarantined" de D08 é avaliado
por um proxy de transcrição (`not_contains` na política insegura).


`session_ref` aceita seletores (EV-2), cada um resolvendo para a sessão
*mais recente* que casa: `latest`, `project:<substr>` (casa
`working_directory`), `window:<AAAA-MM-DD>:<AAAA-MM-DD>` — além de id ou
título exatos como antes.


`devin-evals judge <case> --question "…"` (EV-1, **opt-in**) pede a um LLM
ao vivo que avalie um caso. Não-determinístico, desligado por defeito,
fail-closed: precisa de `DEVIN_BRIDGE_CMD` (usa o `devin-bridge`, que
respeita a policy) e o modelo free salvo `DEVIN_JUDGE_MODEL`. As sessões
são rotuladas `judge:<case>` para o `devin-janitor` as limpar.


`ab-run` (EV-5/G3, **opt-in**) é o gate A/B: uma suíte de tarefas corre
em dois braços (prefixos A vs B), k tentativas por braço, avaliadas pelas
mesmas rubricas determinísticas — o ciclo de prova com-skill/sem-skill.
Consome tokens reais, fail-closed sem `DEVIN_BRIDGE_CMD`; as sessões são
rotuladas `g3-ab:<task>:<variant>:<attempt>` para o janitor.

```bash
devin-evals ab-run --evals evals --tasks evals/tasks \
  --attempts 5 --seed 73001 --max-sessions 80 --yes \
  --sessions-db "$sessions_db" --out g3-report.json
```

A suíte fica num **diretório de manifestos** (padrão `<evals>/tasks`,
caindo para `./tasks`, ou `--tasks DIR`). Dois layouts são aceitos: um
`manifest.json` índice (lista de entradas `{"id", "kind", "type",
"prompt", "dir"|"workspace"}`), ou um `*.json` por tarefa
(`{"id", "kind": "bugfix|feature|refactor", "type": "trigger|control",
"prompt", "workspace"}`). O repo traz um pack hermético de 8 tarefas em
`tasks/` (5 trigger + 3 control — ver `tasks/README.md`). Uma corrida
exige **≥5 trigger + ≥3 control**; as trigger medem o efeito, as control
detectam regressões colaterais. Cada tentativa corre num
`shutil.copytree` próprio do workspace sob `--work-dir` (padrão
`<tmp>/g3-<ts>`) — os originais nunca são tocados e o `_solution/` de
referência nunca é copiado para a tentativa. A ordem é determinística:
tarefas embaralhadas por `--seed`, braços intercalados ABBA/BAAB entre
tarefas para espalhar o drift temporal.

Sucesso = todos os checks passam. O grader padrão repassa a sessão pelas
rubricas (`--sessions-db`, autodetectado): o caso de eval com o id da
tarefa, se existir, senão o pack do `kind` da tarefa. Para packs
herméticos de workspace, `--workspace-check` roda o check
determinístico da própria tarefa dentro da cópia (`pytest tests -q`,
mais `check_structure.py` quando presente) — combinado com a rubrica de
sessão quando um sessions.db resolve.

Caps de orçamento (`--max-sessions`, `--max-total-time`,
`--session-timeout`) abortam a corrida inteira e reportam o que correu;
planos acima do cap exigem `--confirm` (interativo) ou `--yes`
(headless). `--dry-run` imprime o plano completo de graça. Tentativas
com timeout/falha contam como falhas, são registradas à parte e nunca
são repetidas.

**Pré-registro**: tarefas, k, seed, caps e todos os thresholds de
veredicto são congelados no bloco `design` do relatório *antes* da
primeira sessão (`preregistered: true`). O relatório (`g3-report/0.1`,
`--out`) contém só agregados e ids de sessão — nunca conteúdo de sessão.

**Veredictos** (intervalos de Wilson nas taxas dos braços; CI bootstrap
da média dos Δ por tarefa = rate<sub>B</sub> − rate<sub>A</sub> nas
trigger, 10k reamostragens com seed fixa):

| veredicto | condição |
|---|---|
| `regresses` | CI do Δ inteiramente < 0, OU alguma control com Δ ≤ −0.4, OU segurança piora (`denials_b > denials_a` quando `denials_a == 0`) |
| `improves` | CI do Δ nas trigger inteiramente > 0 E sem condição de regressão E calibração ok |
| `no-detectable-effect` | CI do Δ contém 0 E largura ≤ 0.30 |
| `inconclusive` | todo o resto: CI mais largo que 0.30, teto (≥95%) ou piso (≤5%) na baseline, >20% de aborts num braço, calibração ausente/falha quando necessária |

**Calibração**: `improves` ainda exige uma calibração A/A válida — corra
`ab-run --aa` (braço B usa o prefixo do braço A) para medir a taxa de
falsos positivos do próprio harness, e aponte `--calibration
relatorio-anterior.json` para ela. Sem isso, uma corrida que melhoraria
é honestamente reportada `inconclusive`.

**Nota honesta de poder estatístico**: com a suíte mínima (8 tarefas) em
k=5 o CI do Δ fica em ~±0.22 — só efeitos ≥ ~0.25 são detectáveis. Este
gate pega regressões grandes, não melhorias sutis; adicione tarefas, não
tentativas, para estreitar o CI.

Passar `--task` ainda corre o modo simples depreciado (exatamente duas
sessões, rótulos `ab-run:<tag>:<variant>`) — mantido para smoke checks
rápidos, não é um gate.

## Limitações

- **Só replay offline** (M1): avalia sessões gravadas, não lança novas.
  Casos só com `prompt_context` reportam SKIP.
- **Só determinístico** (M1): sem LLM-as-judge; `contains` é substring
  literal e case-sensitive — não distingue semanticamente "all tests pass"
  de "not all tests pass".
- Depende de internals *privados e versionados* do Devin — um bump de
  esquema em `sessions.db` faz o `devin-internals` recusar ruidosamente em
  vez de ler mal.
- `tool_called` infere o nome da tool de `name`/`tool_name`/`tool`/`kind`
  em `tool_call_json`; formatos desconhecidos degradam para "tool never
  called", não para crashes.
- `file_exists` verifica o filesystem *agora* — replay de uma sessão
  antiga cujo workspace foi limpo falha esse check.

## Desenvolvimento

```bash
pip install -e ".[dev]"
python -m pytest     # 177 testes
```

## Quando usar

- Você mudou um prompt, ficheiro de regras ou modelo e quer um sinal numérico de regressão entre sessões gravadas.
- Você quer afirmar comportamento de tool calls — ex.: "tem de chamar `devin_redact` antes de publicar" — como um facto verificável.
- Você precisa de pontuação reproduzível: graders determinísticos tornam re-execuções byte-idênticas, sem juiz LLM envolvido.
- Você quer experimentar sem uma instalação do Devin: `python -m devin_evals.demo demo.db` constrói uma DB de exemplo.

## Quando NÃO usar

- Você precisa de julgamento semântico de texto livre — `contains` é uma substring literal e case-sensitive e não há LLM-as-judge (M1).
- Você precisa de criar novas sessões — isto é replay offline apenas de sessões gravadas.
- O seu agente não é o Devin — a verdade terrestre vem de `sessions.db`/`tool_call_state`.

## FAQ

**Como testo regressões em mudanças nos meus prompts ou regras do Devin?** Defina casos em `evals/*.json` emparelhando um `session_ref` (id ou título da sessão) com uma rubric, e execute `devin-evals run --evals evals --sessions-db <path> --out report`. Cada item da rubric é um grader determinístico sobre os tool calls gravados, por isso re-execuções produzem pontuações idênticas — uma comparação antes/depois real.

**O devin-evals usa um juiz LLM?** Não. Todos os graders (`contains`, `tool_called`, `file_exists`, `exit_code`, `no_secrets`) são checks determinísticos sobre `tool_call_state` e a transcrição. Isso torna as pontuações reproduzíveis mas também literais — não consegue avaliar a qualidade semântica de prosa.

**Posso usar o devin-evals sem o Devin instalado?** Sim, para uma demo: `python -m devin_evals.demo demo.db` cria um sessions.db sintético e o diretório `evals/` incluído contém casos que passam e casos intencionalmente falhos. Para uso real precisa de um `sessions.db` de sessões Devin reais.

## Licença

MIT — vê [LICENSE](LICENSE).


---

Se isso te poupou tempo de depuração, uma ⭐ no repositório ajuda outras pessoas a encontrá-lo.
