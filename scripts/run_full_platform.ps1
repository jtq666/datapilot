param(
    [int]$TargetPort = 18090,
    [int]$WebPort = 5173,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$webRoot = Join-Path $projectRoot "web"
$resourceRoot = "F:\shixi\_resources\data-agent-platform"
$processes = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Assert-Path([string]$Path, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Description not found: $Path"
    }
}

function Start-Child([string]$FilePath, [string[]]$ArgumentList, [string]$WorkingDirectory, [string]$Label) {
    $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru
    $processes.Add($proc)
    Write-Host "$Label started (PID $($proc.Id))."
    return $proc
}

function Test-PortInUse([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Get-DescendantProcessIds([int]$ParentId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        $child.ProcessId
        Get-DescendantProcessIds $child.ProcessId
    }
}

function Stop-ProcessTree([System.Diagnostics.Process]$Process) {
    if (-not $Process) { return }
    $descendants = @(Get-DescendantProcessIds $Process.Id)
    foreach ($id in ($descendants | Sort-Object -Descending)) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }
    if (-not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
}

function Stop-Children {
    foreach ($proc in $processes) {
        Stop-ProcessTree $proc
    }
}

Assert-Path $projectRoot "Project root"
New-Item -ItemType Directory -Path $resourceRoot -Force | Out-Null
$env:TEMP = Join-Path $resourceRoot "tmp"
$env:TMP = $env:TEMP
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null

try {
    Assert-Path $python "Target Python environment"
    if (Test-PortInUse $TargetPort) {
        Write-Host "data_platform API already listening on port $TargetPort; reusing it."
    } else {
        Start-Child $python @(
            "-m", "data_platform.api", "serve", "--port", $TargetPort
        ) $projectRoot "data_platform API"
    }

    if (-not $SkipFrontend) {
        Assert-Path (Join-Path $webRoot "package.json") "Target web package"
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if (-not $npm) { throw "npm.cmd was not found. Use -SkipFrontend or install Node.js." }
        $env:npm_config_cache = Join-Path $resourceRoot "npm-cache"
        if (Test-PortInUse $WebPort) {
            Write-Host "data_platform web already listening on port $WebPort; reusing it."
        } else {
            Start-Child $npm.Source @(
                "run", "dev", "--", "--host", "127.0.0.1", "--port", $WebPort
            ) $webRoot "data_platform web"
        }
    }
    Write-Host ""
    Write-Host "Running services:"
    Write-Host "  Target API:       http://127.0.0.1:$TargetPort/docs"
    if (-not $SkipFrontend) { Write-Host "  Target web:       http://127.0.0.1:$WebPort" }
    Write-Host "Press Ctrl+C to stop only the processes started by this script."
    while ($true) {
        Start-Sleep -Seconds 2
        $dead = $processes | Where-Object { $_.HasExited }
        if ($dead) {
            $dead | ForEach-Object { Write-Warning "Process $($_.Id) exited with code $($_.ExitCode)." }
            break
        }
    }
}
finally {
    Stop-Children
}
