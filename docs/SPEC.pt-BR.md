# SPEC — `devin-qa-pack` (M1)

Versão em português. Canónica (EN): `SPEC.md`.

## 1. Problema

Sessões do Devin terminam com o agente *dizendo* o que entregou: "testes
passaram", "commit `a1b2c3d`", "criei `src/report.html`", "fiz push".
São **afirmações em texto** — um modelo pode escrevê-las tendo ou não
executado as ações. Hoje, verificar isso exige ler a transcrição na mão
ou refazer o trabalho. Quem adota fluxos com agentes não tem como
responder barato: *a sessão fez mesmo o que afirma?*

As ações do agente já ficam gravadas — cada tool call persiste em
`tool_call_state`. Nada cruza as afirmações com esse registo.

## 2. Diferencial Devin-native (e os 3 testes)

**Diferencial:** verifica afirmações contra a *verdade terrestre* do
`tool_call_state` (lido via `devin-internals-spec`) — não contra outro
texto. Uma sessão que diz "testes verdes" precisa mostrar uma chamada
run/execute cujo comando correu um test runner e terminou com sucesso;
"commit `sha`" precisa mostrar o hash num tool call ou no `git log` do
diretório de trabalho em disco.

- **Lado a lado:** viewers de transcrição e auditores de texto comparam
  afirmação com *texto*. Não distinguem "correu pytest e passou" de
  "escreveu que pytest passou". Esta ferramenta checa os tool calls
  gravados — algo que nenhum verificador de texto consegue fazer.
- **Sem Devin:** sem `sessions.db`/`tool_call_state` não há verdade
  terrestre — o diferencial desaparece.
- **Uma frase:** *"Audita se os tool calls de uma sessão Devin sustentam
  o que o agente disse que fez."*

## 3. Escopo

`src/devin_qa_pack/`:

- `claims.py` — extrai afirmações de entrega dos `message_nodes`:
  afirmações de teste (`tests`, com o runner quando citado), de commit
  (`commit` + hash), de ficheiro (`file` + path) e de push (`push`).
  Só mensagens de papel agente; dedup por `(kind, detail)`.
- `verify.py` — resolve cada afirmação contra `tool_call_state`, mais
  `git` quando `sessions.working_directory` é um repo em disco:
  `verified` / `disputed` / `unverifiable`.
- `report.py` — veredito por sessão: `PASS` / `PARTIAL` / `UNVERIFIED`,
  com achados por afirmação.
- `cli.py` — wrapper fino; `paths.py` — local padrão do store.

## 4. Fora de escopo

- Não julga qualidade nem correção do trabalho — só se as afirmações são
  corroboradas.
- Não corre testes nem builds.
- Não escreve em nenhum store do Devin (sempre read-only).
- Não interpreta afirmações fora dos tipos listados.
- Sem MCP, sem watch mode, sem GitHub Action no M1 (fila do M2).

## 5. Semântica afirmação → status

Ver `SPEC.md` §5 — a tabela é a referência canónica. Resumo: chamada
correspondente com sucesso → `verified`; chamada falhada ou ação sem
registo → `disputed`; verdade terrestre ilegível ou impossível de checar
→ `unverifiable`.

Vereditos: `PASS` = tudo verificado · `PARTIAL` = auditoria correu com
disputas/mistos · `UNVERIFIED` = sem afirmações ou nada verificável.

## 6. Interfaces

| Interface | Descrição |
|---|---|
| **Biblioteca** `devin_qa_pack` | `claims.extract_claims` · `verify.verify_claim(s)` · `report.audit_session/audit_all` |
| **CLI** `devin-qa-pack` | `audit --sessions-db <path> --session <id|prefixo>` · `audit --all [--limit N]` · `--json` · auto-deteção de `<data dir>/cli/sessions.db` |
| **Docs** | `SPEC.md` (canónico) + `SPEC.pt-BR.md` |

Exit codes: `0` todas as sessões `PASS` · `1` auditoria correu, alguma
`PARTIAL`/`UNVERIFIED` · `2` auditoria não correu.

## 7. Fixtures e testes

`tests/conftest.py` gera um `sessions.db` via
`devin_internals.fixtures.create_sessions_db()` e estende com linhas
controladas (afirmações + tool calls que confirmam, contradizem ou ficam
inverificáveis). Um repo `git` real em tmp cobre o ramo on-disk.

## 8. Riscos

Formatos `chat_message`/`tool_call_*_json` *unstable* → decode defensivo,
linhas ilegíveis viram `unverifiable`, nunca crash. Read-only; achados
levam paths/hashes/excertos (≤160 chars), nunca dumps de linhas.

## 9. Definição de pronto

Ver `SPEC.md` §9 — checklist cumprida no M1.

## 10. Fila do M2

1. `git fsck`/`reflog` para commits além do `cat-file`.
2. Custo por afirmação (tokens/ACUs por claim verificado).
3. Modo GitHub Action.
4. Publicação PyPI.
