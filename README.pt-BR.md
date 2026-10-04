<div align="center">

<img src="assets/banner.svg" alt="devin-qa-pack" width="100%"/>

<a href="https://github.com/Icaro0310/devin-qa-pack/actions/workflows/tests.yml"><img src="https://github.com/Icaro0310/devin-qa-pack/actions/workflows/tests.yml/badge.svg" alt="tests"/></a>


</div>

# devin-qa-pack

> **Projeto comunitário não oficial.** Sem afiliação, endosso ou patrocínio da
> Cognition AI. "Devin" é marca registada da Cognition AI.

**[English](README.md)** · Português (BR)

Auditoria de QA para sessões Devin: verifica se o que a sessão *afirma*
ter entregado é sustentado pelo que os tool calls *realmente fizeram* —
testes corridos, commits criados, ficheiros escritos, push feito,
status HTTP devolvidos — e dá um veredito por sessão:
`PASS` / `PARTIAL` / `UNVERIFIED`.

## O problema

Sessões Devin terminam com o agente dizendo "testes passaram", "commit
`a1b2c3d`", "push feito". São afirmações em texto — um modelo pode
escrevê-las tendo ou não executado as ações. Verificar hoje significa ler
a transcrição na mão ou confiar no resumo. Quem adota fluxos com agentes
precisa de uma forma barata e repetível de responder: *a sessão fez mesmo
o que afirma?*

## Trabalho anterior (prior art)

Viewers de transcrição/sessão (incluindo a UI do próprio Devin) mostram
*o que aconteceu*; checks de CI verificam resultados depois do facto;
nenhum cruza as afirmações de entrega do agente com as ações gravadas. A
ideia geral — comparar intenção declarada com comportamento observado —
é antiga (auditorias manifest-vs-manifest, `git fsck`, atestações tipo
proveniência SLSA). O que não existia: aplicar isso às afirmações do
agente vs. o seu próprio log de tool calls persistido.

## O que o torna Devin-native

O Devin persiste cada tool call em `sessions.db → tool_call_state`. Esta
ferramenta lê essa tabela via
[devin-internals-spec](https://github.com/Icaro0310/devin-internals-spec)
e trata-a como **verdade terrestre**: "testes passaram" exige uma chamada
run/execute que correu um test runner e terminou com sucesso; "commit
`sha`" exige o hash num call ou no `git log`. Auditores que só leem texto
*não conseguem* fazer isto — tire a store do Devin e a verificação
desaparece.

## Instalação

Requer Python ≥ 3.10 e `pipx`. **Windows (PowerShell):** instale `pipx` com `py -m pip install --user pipx`, execute `py -m pipx ensurepath` e reabra o terminal. **Linux (Debian/Ubuntu):** execute `sudo apt install pipx python3-venv` e `pipx ensurepath`; reabra o terminal. Noutras distribuições Linux, instale `pipx` pelo gestor de pacotes.

```bash
pipx install "devin-qa-pack @ git+https://github.com/Icaro0310/devin-qa-pack.git"
```

(Publicação no PyPI está na fila do M2.)

## Uso

```bash
# auditar uma sessão (id exato ou prefixo único)
devin-qa-pack audit --session <id> --sessions-db caminho/sessions.db

# auditar tudo, mais recentes primeiro
devin-qa-pack audit --all --limit 20 --json

# relatório agregado: um ficheiro HTML estático autocontido (CSS inline,
# sem JS, sem assets externos — abre offline)
devin-qa-pack report --sessions-db caminho/sessions.db --out report.html

# auditoria ao vivo no SessionEnd: audita SÓ a sessão que acabou de
# terminar (modo hook — escreve um ficheiro JSON lateral, nunca toca
# na sessão)
devin-qa-pack session-end
```

O subcomando `report` audita todas as sessões (ou uma com `--session`,
limitando com `--limit`) e escreve um único ficheiro HTML determinístico:
contagem de vereditos, tabela por sessão e detalhamento por afirmação
com evidência e o excerto de origem de cada claim verificado.

`--sessions-db` pode ser omitido. A ferramenta auto-deteta
`%APPDATA%/devin/cli/sessions.db` no Windows e
`$XDG_DATA_HOME/devin/cli/sessions.db` no Linux (por omissão
`~/.local/share/devin/cli/sessions.db`). Sempre read-only.

Exit codes: `0` todas `PASS` · `1` alguma `PARTIAL`/`UNVERIFIED` ·
`2` auditoria não correu.

## Hook SessionEnd (auditoria ao vivo)

`devin-qa-pack session-end` audita **apenas a sessão que acabou de
terminar** e escreve o veredito num **ficheiro lateral** JSON — nunca na
transcrição nem em nenhuma store do Devin. Ordem de resolução da sessão:

1. `--session-id <id>` (id exato ou prefixo único)
2. o campo `session_id` do payload JSON do hook no stdin
3. a variável `DEVIN_SESSION_ID` (exportada pelo dispatcher de hooks)
4. a sessão mais recentemente ativa no `sessions.db`

O ficheiro lateral vai por defeito para
`<data-dir>/qa/<session-id>.json` (o mesmo data-dir de plataforma da
auto-detecção da store; `--data-dir` e `--out` substituem) e contém
`{session_id, verdict, claims: [...], audited_at}` — o mesmo formato de
claims do `audit --json`. Também imprime um resumo de uma linha.
`--limit N` limita o número de afirmações verificadas.

**Fail-soft:** `session-end` sai sempre com `0` depois de correr — o
veredito viaja no ficheiro lateral, por isso o hook nunca pode falhar a
sessão hospedeira. Uma sessão que não pode ser resolvida produz um
veredito `SKIPPED` (ainda escrito no ficheiro lateral quando o id é
conhecido). Saídas não-zero ficam reservadas a erros de uso (`2`), como
nos outros subcomandos.

Regista-o como hook `SessionEnd` (entrada hooks.json para o dispatcher
de hooks do `devin-powerups` — ele passa o payload do hook em JSON no
stdin e exporta `DEVIN_SESSION_ID`):

```json
{
  "SessionEnd": [
    {
      "matcher": "",
      "hooks": [
        {
          "type": "command",
          "command": "devin-qa-pack session-end",
          "timeout": 30
        }
      ]
    }
  ]
}
```

## Funciona só com o Devin (modo Devin-only)

O devin-qa-pack é uma auditoria offline e somente-leitura de sessões Devin
gravadas. Nunca chama um LLM, nunca toca na rede e nunca escreve nas stores
do Devin — uma escolha segura para máquinas restritas.

## Suporte de plataformas

Testado em **Windows e Linux** (o CI corre em `windows-latest` +
`ubuntu-latest`). A base CLI é auto-detetada a partir de
`%APPDATA%/devin/cli/sessions.db` no Windows e
`$XDG_DATA_HOME/devin/cli/sessions.db` no Linux (por omissão
`~/.local/share/devin/cli/sessions.db`). A estrutura antiga `~/.config/devin`
também é verificada. macOS usa `~/Library/Application Support/devin/`. Usa
`--sessions-db` para sobrepor.

## Limitações

- Os payloads `chat_message` e `tool_call_*_json` são formatos
  **unstable** (ver SCHEMA.md do devin-internals-spec). O decode é
  defensivo; linhas ilegíveis resolvem afirmações como `unverifiable`,
  nunca `disputed`.
- A extração de afirmações é heurística: procura frases de entrega
  ("tests passed", "committed <sha>", "created <path>", "pushed",
  "the API returned 200"). Afirmações ditas de outra forma não são
  extraídas — a auditoria então reporta `UNVERIFIED`, não um `PASS` falso.
- Afirmações de status HTTP são conferidas só contra a saída gravada dos
  tool calls: `verified` quando um status registado bate, `disputed`
  quando as saídas registam outro status e `unverifiable` quando nenhuma
  saída regista status. Nenhum pedido é repetido na rede.
- Verificações de ficheiro/commit usam `sessions.working_directory` só
  quando existe em disco e é um repo git — senão dependem só da
  evidência dos tool calls.
- Verifica *que* as ações aconteceram, não que o trabalho é bom.
- Read-only, offline; sem monitorização em tempo real nem MCP (M2).

## Desenvolvimento

```bash
pip install -e ".[dev]"
python -m pytest
```

## Quando usar

- Você quer verificar que uma sessão Devin terminada realmente correu testes, criou commits, escreveu ficheiros ou fez push — e não apenas afirmou que o fez.
- Você está a fazer gate de output de agentes em CI e precisa de um veredito legível por máquina (`PASS`/`PARTIAL`/`UNVERIFIED`) com exit codes.
- Você quer auditar muitas sessões de uma vez, offline, sem enviar transcrições para um LLM — e opcionalmente publicar um relatório HTML estático (`devin-qa-pack report`).
- Você está numa máquina restrita: a ferramenta é read-only e nunca toca na rede.

## Quando NÃO usar

- Você precisa de uma revisão de qualidade ou correção de código — ele verifica que as ações aconteceram, não que o trabalho é bom.
- Você precisa de monitorização em tempo real ou de um servidor MCP (no roadmap M2).
- O seu agente não é o Devin — a verdade terrestre vem do `sessions.db` do Devin.

## FAQ

**Como verifico que uma sessão Devin realmente correu os testes que afirma?** Execute `devin-qa-pack audit --session <id>`. Ele lê as linhas `tool_call_state` da sessão no `sessions.db`, extrai afirmações de entrega como "tests passed" e confere cada uma contra os tool calls registados — uma afirmação sem evidência correspondente resolve para `UNVERIFIED` ou `PARTIAL`, não `PASS`.

**O devin-qa-pack precisa de acesso à rede ou de uma API key?** Não. É uma auditoria totalmente offline e read-only do `sessions.db` local. Nunca chama um LLM, nunca envia dados para lado nenhum e nunca escreve nos stores do Devin.

**Onde é que o devin-qa-pack encontra o sessions.db?** Deteta automaticamente `%APPDATA%/devin/cli/sessions.db` no Windows, `$XDG_DATA_HOME/devin/cli/sessions.db` no Linux (`~/.local/share/devin/cli/sessions.db` por defeito), `~/Library/Application Support/devin/` no macOS, mais um layout legado `~/.config/devin`. Substitua com `--sessions-db`.

## Licença

MIT — vê [LICENSE](LICENSE).


---

Se isso te poupou tempo de depuração, uma ⭐ no repositório ajuda outras pessoas a encontrá-lo.
