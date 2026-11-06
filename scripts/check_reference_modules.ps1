param(
    [string]$BaseUrl = "http://127.0.0.1:5670"
)

$ErrorActionPreference = "Stop"
$BaseUrl = $BaseUrl.TrimEnd("/")
$checks = @(
    @{ Name = "web"; Path = "/" },
    @{ Name = "core"; Path = "/api/health" },
    @{ Name = "chat"; Path = "/api/v1/chat/dialogue/health" },
    @{ Name = "observability"; Path = "/api/v1/observability/health" },
    @{ Name = "datasource"; Path = "/api/v2/serve/health" },
    @{ Name = "knowledge"; Path = "/api/v2/serve/knowledge/health" },
    @{ Name = "awel"; Path = "/api/v1/serve/awel/health" },
    @{ Name = "model"; Path = "/api/v2/serve/model/health" },
    @{ Name = "feedback"; Path = "/api/v1/conv/feedback/health" },
    @{ Name = "file"; Path = "/api/v2/serve/file/health" },
    @{ Name = "evaluation"; Path = "/api/v2/serve/evaluate/health" },
    @{ Name = "connector"; Path = "/api/v2/serve/connectors/types" },
    @{ Name = "scheduled-tasks"; Path = "/api/v2/serve/scheduled-tasks/" },
    @{ Name = "session-files"; Path = "/api/v1/agent/files/capabilities" }
)

$failed = [System.Collections.Generic.List[string]]::new()
foreach ($check in $checks) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing ($BaseUrl + $check.Path) -TimeoutSec 15
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
            Write-Host ("[OK]   {0,-16} {1}" -f $check.Name, $check.Path)
        } else {
            Write-Host ("[FAIL] {0,-16} HTTP {1}" -f $check.Name, $response.StatusCode)
            $failed.Add($check.Name)
        }
    } catch {
        Write-Host ("[FAIL] {0,-16} {1}" -f $check.Name, $_.Exception.Message)
        $failed.Add($check.Name)
    }
}

if ($failed.Count -gt 0) {
    throw "Reference module checks failed: $($failed -join ', ')"
}
Write-Host "All reference module checks passed."
