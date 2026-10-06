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

# Pinned tools: release tags where a release exists, commit SHAs otherwise
# (qa-pack is pinned to a post-v0.1.0 SHA because v0.1.0 predates the
# terminal_exit fix this demo relies on — move to the next tag when cut).
$GH = "https://github.com/Icaro0310"
$DREAM   = @("--from", "git+$GH/devin-dream@4f0e6e0761d61fda33d89a4c98086bff53d9519c", "devin-dream")
$INSPECT = @("--from", "git+$GH/devin-internals-spec@v0.3.0", "devin-inspect")
$QA      = @("--from", "git+$GH/devin-qa-pack@9e4456c8366c6696524388544600945612f5bdc1", "devin-qa-pack")
$EVALS   = @("--from", "git+$GH/devin-evals@d2355703723fbfc30738705d7eddd7b0ce0b26f2", "devin-evals")

# Warm the uv cache so first-build noise stays out of the demo output.
Write-Host "  [setup] fetching tools (one-time; uv cache)"
function Test-ToolFetch { param([array]$t)
  Uvx @t --help | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Error "failed to fetch: $($t[-1])"; exit 127 }
}
Test-ToolFetch $DREAM; Test-ToolFetch $INSPECT; Test-ToolFetch $QA; Test-ToolFetch $EVALS
Write-Host "        + tools ready"

Write-Host "  [1/4] Generating labeled sessions            (devin-dream)"
Uvx @DREAM unit --out "$Out\sessions" --defect D01 D02 D03 | Out-Null
Write-Host "        + d01: claims a fix, leaves no evidence"
Write-Host "        + d02: claims tests pass, pytest exited 1"
Write-Host "        + d03: claims tests pass, pytest exited 0"

Write-Host "  [2/4] Checking the store contract            (devin-inspect)"
$schema = Uvx @INSPECT schema "$Out\sessions\d03\sessions.db" | ConvertFrom-Json
Write-Host "        + sessions.db is schema v$($schema.schema_version), supported, verified"

Write-Host "  [3/4] Auditing agent claims                  (devin-qa-pack)"
$qaMatched = 0; $qaTotal = 0; $qaBad = @()
foreach ($d in 'd01','d02','d03') {
  $db = "$Out\sessions\$d\sessions.db"
  $exp = (Get-Content "$Out\sessions\$d\expected.json" | ConvertFrom-Json).expected.'devin-qa-pack'
  $line = (Uvx @QA audit --sessions-db $db --all 2>$null | Select-Object -First 1)
  $got = ("$line" -split ' ')[0]
  $qaTotal++
  if ($got -notin 'PASS','PARTIAL','UNVERIFIED') {
    $got = "ERROR"; $line = "audit produced no verdict (harness failure)"
  }
  $mark = "+"
  if ($got -eq $exp) { $qaMatched++ } else { $mark = "x"; $qaBad += "        x  $d  $line" }
  Write-Host ("        {0}  {1,-10} {2}" -f $mark, $d, $line)
}

# The first two rubrics are SUPPOSED to fail: the defect is real, and the
# grader detecting it is the expected outcome — not a demo failure.
Write-Host "  [4/4] Grading with a deterministic rubric    (devin-evals)"
$evMatched = 0; $evTotal = 0; $evBad = @()
foreach ($d in 'd01','d02','d03') {
  New-Item -ItemType Directory -Force -Path "$Out\evals-$d" | Out-Null
  $caseFile = Get-Item "$Here\evals\golden-$d-*.json"
  Copy-Item $caseFile "$Out\evals-$d\"
  $expStatus = (Get-Content $caseFile | ConvertFrom-Json).expected_status
  $line = (Uvx @EVALS run --evals "$Out\evals-$d" `
    --sessions-db "$Out\sessions\$d\sessions.db" `
    --out "$Out\reports\$d" 2>$null | Select-String "^(PASS|FAIL)" | Select-Object -First 1)
  $grade = ("$line" -split ' ')[0].ToLower()
  $evTotal++
  if ($grade -notin 'pass','fail') {
    $grade = "error"; $line = "eval produced no verdict (harness failure)"
  }
  $mark = "+"
  if ($grade -eq $expStatus) { $evMatched++ } else { $mark = "x"; $evBad += "        x  $line" }
  Write-Host ("        {0}  {1}  (rubric expected: {2})" -f $mark, "$line", $expStatus)
}

Write-Host ""
Write-Host "  RESULT"
Write-Host "  ------------------------------------------------"
Write-Host "  $qaMatched/$qaTotal QA verdicts matched their labels"
Write-Host "  $evMatched/$evTotal evaluation outcomes matched their labels"
Write-Host ""
Write-Host "  The agent's narrative was confident in all three sessions."
Write-Host "  The tool-call record told a different story — and the stack"
Write-Host "  caught it. That is the product: claims vs evidence."
Write-Host ""
Write-Host "  Artifacts:"
Write-Host "    $Out\sessions\   generated sessions.db + expected.json per defect"
Write-Host "    $Out\reports\    eval grading reports per defect"
if ($qaMatched -ne $qaTotal -or $evMatched -ne $evTotal) {
  Write-Host "`n  x Demo failed — a verdict or eval outcome did not match its label:"
  $qaBad + $evBad | ForEach-Object { Write-Host $_ }
  exit 1
}
Write-Host "`n  + Demo completed successfully."
