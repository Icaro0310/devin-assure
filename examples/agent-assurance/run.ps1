# agent-assurance — reproducible end-to-end demo of the devin-* stack.
#
#   devin-dream generates controlled sessions with known defects
#     -> devin-inspect proves the store parses (schema contract)
#     -> devin-qa-pack audits the agent's claims against tool-call evidence
#     -> devin-evals replays a deterministic rubric on the same session
#
# Usage: .\run.ps1 [output-dir]   (default: %TEMP%\agent-assurance-*)
param([string]$Out)

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Out) { $Out = Join-Path $env:TEMP ("agent-assurance-" + (Get-Random)) }
New-Item -ItemType Directory -Force -Path "$Out\sessions", "$Out\reports" | Out-Null

foreach ($tool in 'devin-dream','devin-inspect','devin-qa-pack','devin-evals') {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    Write-Error "missing: $tool   (see README.md for install)"
    exit 127
  }
}

Write-Host "==> 1/4 devin-dream: generate labeled sessions"
devin-dream unit --out "$Out\sessions" --defect D01 D02 D03

Write-Host "`n==> 2/4 devin-inspect: schema contract on a generated store"
devin-inspect schema "$Out\sessions\d03\sessions.db"
devin-inspect sessions "$Out\sessions\d03\sessions.db"

Write-Host "`n==> 3/4 devin-qa-pack: audit claims vs tool-call evidence"
foreach ($d in 'd01','d02','d03') {
  $db = "$Out\sessions\$d\sessions.db"
  $exp = (Get-Content "$Out\sessions\$d\expected.json" | ConvertFrom-Json).expected.'devin-qa-pack'
  $line = (devin-qa-pack audit --sessions-db $db --all 2>&1 | Select-Object -First 1)
  $got = ($line -split ' ')[0]
  $mark = if ($got -eq $exp) { "OK" } else { "MISMATCH" }
  Write-Host ("  {0,-10} {1} (expected {2})`n      {3}" -f $d, $mark, $exp, $line)
}

Write-Host "`n==> 4/4 devin-evals: deterministic rubric grading"
foreach ($d in 'd01','d02','d03') {
  New-Item -ItemType Directory -Force -Path "$Out\evals-$d" | Out-Null
  Copy-Item "$Here\evals\golden-$d-*.json" "$Out\evals-$d\"
  devin-evals run --evals "$Out\evals-$d" `
    --sessions-db "$Out\sessions\$d\sessions.db" `
    --out "$Out\reports\$d" 2>&1 | Select-Object -Last 2
}

Write-Host "`nDone. Artifacts in $Out"
Write-Host "  sessions\  generated sessions.db + expected.json per defect"
Write-Host "  reports\   devin-evals grading reports"
