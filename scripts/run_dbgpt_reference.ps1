param(
    [string]$Config = "configs/dbgpt-proxy-deepseek-local.toml",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$referenceRoot = Join-Path $projectRoot "vendor\dbgpt-upstream"

if (-not (Test-Path -LiteralPath $referenceRoot)) {
    throw "Missing vendor/dbgpt-upstream. Extract the reference snapshot first."
}

$runnerCandidates = @(
    (Join-Path $referenceRoot ".venv\Scripts\dbgpt.exe"),
    "F:\shixi\DB-GPT\.venv\Scripts\dbgpt.exe"
)
$runner = $runnerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $runner) {
    throw "No DB-GPT runtime found. Install the reference dependencies or set up vendor/dbgpt-upstream/.venv."
}

# The fallback environment may contain an editable DB-GPT checkout at
# F:\shixi\DB-GPT. Put this snapshot's package sources first so the command
# cannot silently run a different checkout.
$vendorSourcePaths = Get-ChildItem -LiteralPath (Join-Path $referenceRoot "packages") -Directory |
    ForEach-Object { Join-Path $_.FullName "src" } |
    Where-Object { Test-Path -LiteralPath $_ }
$existingPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = if ($existingPythonPath) {
    (($vendorSourcePaths + $existingPythonPath) -join ";")
} else {
    ($vendorSourcePaths -join ";")
}

$resourceRoot = "F:\shixi\_resources\data-agent-platform\dbgpt"
New-Item -ItemType Directory -Path $resourceRoot -Force | Out-Null
$env:DBGPT_HOME = $resourceRoot
$env:DBGPT_LOG_DIR = Join-Path $resourceRoot "logs"
$env:DBGPT_OBSERVABILITY_SQLITE_PATH = Join-Path $env:DBGPT_LOG_DIR "observability.db"
$env:DBGPTS_HOME = Join-Path $resourceRoot "dbgpts"
$env:DBGPTS_REPO_HOME = Join-Path $env:DBGPTS_HOME "repos"
$env:SENTENCE_TRANSFORMERS_HOME = Join-Path $resourceRoot "models"
$env:TEMP = Join-Path $resourceRoot "tmp"
$env:TMP = $env:TEMP
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Path $env:DBGPT_LOG_DIR, $env:TEMP -Force | Out-Null

if (-not $env:DEEPSEEK_API_KEY) {
    throw "DEEPSEEK_API_KEY is not set. Set it in the current PowerShell session before starting the DeepSeek reference configuration."
}

$configPath = if ([System.IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $referenceRoot $Config }
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Reference config not found: $configPath"
}
$runConfig = $configPath
if ($Port -gt 0) {
    $runConfig = Join-Path $resourceRoot "reference-$Port.toml"
    $configText = Get-Content -LiteralPath $configPath -Raw
    $configText = [regex]::Replace($configText, '(?m)^port\s*=\s*\d+\s*$', "port = $Port")
    Set-Content -LiteralPath $runConfig -Value $configText -Encoding UTF8
}

Push-Location $referenceRoot
try {
    Write-Host "DB-GPT source: $referenceRoot"
    & $runner start webserver --config $runConfig
    if ($LASTEXITCODE -ne 0) {
        throw "DB-GPT reference process exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
