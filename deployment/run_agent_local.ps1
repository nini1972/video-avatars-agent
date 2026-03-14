$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Join-Path $scriptDir ".."
Set-Location $repoRoot

$envFile = ".env"
if (-not (Test-Path $envFile)) {
    throw "Missing .env file. Create it from .env-template first."
}

Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $parts = $line -split "=", 2
    if ($parts.Count -ne 2) { return }
    $key = $parts[0].Trim()
    $value = $parts[1].Trim().Trim('"')
    [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
}

$pythonExe = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

if ([string]::IsNullOrWhiteSpace($env:AGENT_ENGINE_ID)) {
    $engineId = & $pythonExe "deployment/get_agent_engine.py" --agent-name "$env:AGENT_ENGINE_NAME" --project-id "$env:GOOGLE_CLOUD_PROJECT" --location "$env:GOOGLE_CLOUD_REGION" | Select-Object -Last 1
    if ([string]::IsNullOrWhiteSpace($engineId)) {
        throw "Failed to resolve AGENT_ENGINE_ID"
    }
    $env:AGENT_ENGINE_ID = $engineId
    Add-Content -Path $envFile -Value "AGENT_ENGINE_ID=\"$engineId\""
}

& adk web --port 8081 --artifact_service_uri "gs://$env:AI_ASSETS_BUCKET" --session_service_uri "agentengine://$env:AGENT_ENGINE_ID" ./agents
