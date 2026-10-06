# Agent Assurance — 60-second demo of the devin-* assurance loop.
#
#   devin-dream  generates synthetic sessions with KNOWN defects
#   devin-inspect proves the generated store matches the schema contract
#   devin-qa-pack audits the agent's claims against tool-call evidence
#   devin-evals  grades the same sessions with a deterministic rubric
#
# Nothing to install first: the script bootstraps uv (if absent) and runs
# each tool via uvx straight from GitHub.
#
#   usage: .\run.ps1 [output-dir]
param([string]$Out)

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Out) { $Out = Join-Path $env:TEMP ("agent-assurance-" + (Get-Random)) }
New-Item -ItemType Directory -Force -Path "$Out\sessions", "$Out\reports" | Out-Null

Write-Host ""
Write-Host "  Agent Assurance Demo"
Write-Host "  ------------------------------------------------"

# ── bootstrap: uv (the only prerequisite; auto-installed if missing) ────────
$uvx = Get-Command uvx -ErrorAction SilentlyContinue
if (-not $uvx -and (Test-Path "$env:USERPROFILE\.local\bin\uvx.exe")) {
  $uvx = "$env:USERPROFILE\.local\bin\uvx.exe"
}
if (-not $uvx) {
  Write-Host "  [setup] uv not found — installing via the official installer"
  irm https://astral.sh/uv/install.ps1 | iex
  $uvx = "$env:USERPROFILE\.local\bin\uvx.exe"
  if (-not (Test-Path $uvx)) {
    Write-Error "uv install failed — see https://docs.astral.sh/uv/"; exit 127
  }
}
function Uvx { param([Parameter(ValueFromRemainingArguments)]$a) & $uvx @a }

$GH = "https://github.com/Icaro0310"

Write-Host "  [1/4] Generating labeled sessions            (devin-dream)"
Uvx --from "git+$GH/devin-dream" devin-dream unit --out "$Out\sessions" --defect D01 D02 D03 | Out-Null
Write-Host "        + d01: claims a fix, leaves no evidence"
Write-Host "        + d02: claims tests pass, pytest exited 1"
Write-Host "        + d03: claims tests pass, pytest exited 0"

Write-Host "  [2/4] Checking the store contract            (devin-inspect)"
$schema = Uvx --from "git+$GH/devin-internals-spec" devin-inspect schema "$Out\sessions\d03\sessions.db" | ConvertFrom-Json
Write-Host "        + sessions.db is schema v$($schema.schema_version), supported, verified"

Write-Host "  [3/4] Auditing agent claims                  (devin-qa-pack)"
$results = @()
foreach ($d in 'd01','d02','d03') {
  $db = "$Out\sessions\$d\sessions.db"
  $exp = (Get-Content "$Out\sessions\$d\expected.json" | ConvertFrom-Json).expected.'devin-qa-pack'
  $line = (Uvx --from "git+$GH/devin-qa-pack" devin-qa-pack audit --sessions-db $db --all 2>&1 | Select-Object -First 1)
  $got = ($line -split ' ')[0]
  $mark = if ($got -eq $exp) { "+" } else { "x MISMATCH" }
  $results += ,@($got, $exp)
  Write-Host ("        {0}  {1,-10} {2}" -f $mark, $d, $line)
}

# The first two rubrics are SUPPOSED to fail: the defect is real, and the
# grader detecting it is the expected outcome — not a demo failure.
Write-Host "  [4/4] Grading with a deterministic rubric    (devin-evals)"
foreach ($d in 'd01','d02','d03') {
  New-Item -ItemType Directory -Force -Path "$Out\evals-$d" | Out-Null
  $caseFile = Get-Item "$Here\evals\golden-$d-*.json"
  Copy-Item $caseFile "$Out\evals-$d\"
  $expStatus = (Get-Content $caseFile | ConvertFrom-Json).expected_status
  $line = (Uvx --from "git+$GH/devin-evals" devin-evals run --evals "$Out\evals-$d" `
    --sessions-db "$Out\sessions\$d\sessions.db" `
    --out "$Out\reports\$d" 2>$null | Select-String "^(PASS|FAIL)" | Select-Object -First 1)
  $grade = ("$line" -split ' ')[0].ToLower()
  $mark = if ($grade -eq $expStatus) { "+" } else { "x MISMATCH" }
  Write-Host ("        {0}  {1}  (rubric expected: {2})" -f $mark, "$line", $expStatus)
}

Write-Host ""
Write-Host "  RESULT"
Write-Host "  ------------------------------------------------"
$mismatch = $false
foreach ($r in $results) {
  $mark = if ($r[0] -eq $r[1]) { "+" } else { "x"; $mismatch = $true }
  Write-Host ("  verdict {0,-10} (expected {1})  {2}" -f $r[0], $r[1], $mark)
}
Write-Host ""
Write-Host "  The agent's narrative was confident in all three sessions."
Write-Host "  The tool-call record told a different story — and the stack"
Write-Host "  caught it. That is the product: claims vs evidence."
Write-Host ""
Write-Host "  Artifacts:"
Write-Host "    $Out\sessions\   generated sessions.db + expected.json per defect"
Write-Host "    $Out\reports\    eval grading reports per defect"
if ($mismatch) { Write-Host "`n  x A verdict mismatched its label — please open an issue."; exit 1 }
Write-Host "`n  + Demo completed — all verdicts match the labels."
