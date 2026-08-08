[CmdletBinding()]
param(
    [string]$TopicConfig = "configs/topics/broad-llm-agents.yaml",
    [string]$LogicalDate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepositoryRoot

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
$Uv = if ($null -ne $UvCommand) { $UvCommand.Source } else { "D:\Tools\uv\uv.exe" }
if (-not (Test-Path -LiteralPath $Uv) -and $null -eq $UvCommand) {
    throw "uv was not found on PATH or at D:\Tools\uv\uv.exe."
}
if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) {
    throw "DATABASE_URL is required to run the Daily Job."
}
if (-not (Test-Path -LiteralPath $TopicConfig)) {
    throw "Topic configuration '$TopicConfig' does not exist."
}

$Arguments = @(
    "run", "--frozen", "--python", "3.13.13",
    "paper-harness-daily", "--topic-config", $TopicConfig
)
if (-not [string]::IsNullOrWhiteSpace($LogicalDate)) {
    try {
        $ParsedDate = [DateTime]::ParseExact(
            $LogicalDate,
            "yyyy-MM-dd",
            [Globalization.CultureInfo]::InvariantCulture
        )
    }
    catch {
        throw "LogicalDate must use YYYY-MM-DD."
    }
    $Arguments += @("--logical-date", $ParsedDate.ToString("yyyy-MM-dd"))
}

& $Uv @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Daily ingestion failed with exit code $LASTEXITCODE."
}
