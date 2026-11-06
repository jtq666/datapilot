param(
    [ValidateSet("reference", "target", "both")]
    [string]$Mode = "reference",
    [int]$ReferencePort = 5670,
    [int]$TargetPort = 18090,
    [int]$WebPort = 5173,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$referenceRoot = Join-Path $projectRoot "vendor\dbgpt-upstream"
$referenceCommit = "953da6b23fb0be5cfde490944d8f735cd16ac586"
$legacyReferenceRoot = "F:\shixi\DB-GPT"
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
    $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -PassThru
    $processes.Add($proc)
    Write-Host "$Label started (PID $($proc.Id))."
    return $proc
}

function Test-PortInUse([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Get-PortCommandLine([int]$Port) {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $connection) { return $null }
    return (Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue).CommandLine
}

function Find-FreePort([int]$StartPort) {
    $port = $StartPort
    while (Test-PortInUse $port) { $port++ }
    return $port
}

function Test-SameReferenceCheckout([string]$CommandLine) {
    if ($CommandLine -and $CommandLine.Contains("vendor\dbgpt-upstream")) { return $true }
    if (-not ($CommandLine -and $CommandLine.Contains($legacyReferenceRoot))) { return $false }
    $commit = (& git -C $legacyReferenceRoot rev-parse HEAD 2>$null).Trim()
    return $commit -eq $referenceCommit
}

function Get-PowerShellExecutable {
    $candidate = Get-Command pwsh.exe -ErrorAction SilentlyContinue
    if (-not $candidate) { $candidate = Get-Command powershell.exe -ErrorAction SilentlyContinue }
    if (-not $candidate) { throw "No PowerShell executable was found for the child DB-GPT process." }
    return $candidate.Source
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
$env:DBGPT_HOME = Join-Path $resourceRoot "dbgpt"
$env:DBGPT_LOG_DIR = Join-Path $env:DBGPT_HOME "logs"
$env:TEMP = Join-Path $resourceRoot "tmp"
$env:TMP = $env:TEMP
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null

try {
    $effectiveReferencePort = $ReferencePort
    if ($Mode -eq "reference" -or $Mode -eq "both") {
        Assert-Path (Join-Path $projectRoot "scripts\run_dbgpt_reference.ps1") "DB-GPT launcher"
        if (Test-PortInUse $effectiveReferencePort) {
            $ownerCommand = Get-PortCommandLine $effectiveReferencePort
            if (Test-SameReferenceCheckout $ownerCommand) {
                Write-Host "DB-GPT reference already listening on port $effectiveReferencePort; reusing the matching source checkout."
            } else {
                $oldPort = $effectiveReferencePort
                $effectiveReferencePort = Find-FreePort ($effectiveReferencePort + 1)
                Write-Warning "Port $oldPort is occupied by another process; keeping it untouched and starting the vendor snapshot on $effectiveReferencePort."
                if (-not $env:DEEPSEEK_API_KEY) {
                    throw "DEEPSEEK_API_KEY is not set. The reference UI can only be started after the key is provided in this PowerShell session."
                }
                $referenceScript = Join-Path $projectRoot "scripts\run_dbgpt_reference.ps1"
                $referencePowerShell = Get-PowerShellExecutable
                Start-Child $referencePowerShell @(
                    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $referenceScript,
                    "-Port", $effectiveReferencePort
                ) $projectRoot "DB-GPT reference"
            }
        } else {
            if (-not $env:DEEPSEEK_API_KEY) {
                throw "DEEPSEEK_API_KEY is not set. The reference UI can only be started after the key is provided in this PowerShell session."
            }
            $referenceScript = Join-Path $projectRoot "scripts\run_dbgpt_reference.ps1"
            $referencePowerShell = Get-PowerShellExecutable
            Start-Child $referencePowerShell @(
                "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $referenceScript,
                "-Port", $effectiveReferencePort
            ) $projectRoot "DB-GPT reference"
        }
    }

    if ($Mode -eq "target" -or $Mode -eq "both") {
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
    }

    Write-Host ""
    Write-Host "Running services:"
    if ($Mode -eq "reference" -or $Mode -eq "both") { Write-Host "  DB-GPT reference: http://127.0.0.1:$effectiveReferencePort" }
    if ($Mode -eq "target" -or $Mode -eq "both") {
        Write-Host "  Target API:       http://127.0.0.1:$TargetPort/docs"
        if (-not $SkipFrontend) { Write-Host "  Target web:       http://127.0.0.1:$WebPort" }
    }
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
