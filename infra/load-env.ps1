param([switch]$UseExistingApiKeys)
$project = Split-Path -Parent $PSScriptRoot
function Read-EnvFile([string]$path) {
    $settings = @{}
    foreach ($line in Get-Content -LiteralPath $path) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $settings[$Matches[1]] = $Matches[2].Trim().Trim('"', "'")
        }
    }
    return $settings
}
$settings = Read-EnvFile (Join-Path $project '.env')
foreach ($name in $settings.Keys) {
    if ($settings[$name] -and -not [Environment]::GetEnvironmentVariable($name)) {
        [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
    }
}
if ($UseExistingApiKeys) {
    $oldPath = Join-Path (Split-Path -Parent $project) 'fastapi_starter\.env'
    if (Test-Path -LiteralPath $oldPath) {
        $existing = Read-EnvFile $oldPath
        if (-not $env:DEEPSEEK_API_KEY -and $existing['LLM_BASE_URL'] -match '^https://api\.deepseek\.com(?:/|$)') {
            $env:DEEPSEEK_API_KEY = $existing['LLM_API_KEY']
        }
        if (-not $env:JINA_API_KEY -and $existing['EMBEDDING_API_KEY'] -match '^jina_') {
            $env:JINA_API_KEY = $existing['EMBEDDING_API_KEY']
        }
    }
}
