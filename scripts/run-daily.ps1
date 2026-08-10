[CmdletBinding()]
param(
    [ValidateSet(
        "ingest-arxiv",
        "analyze-papers",
        "historical-backfill",
        "search-related",
        "compare-papers"
    )]
    [string]$Operation = "ingest-arxiv",
    [string]$TopicConfig = "configs/topics/broad-llm-agents.yaml",
    [string]$LogicalDate,
    [string[]]$OperationArgument = @()
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

$OperationsUsingTopicConfig = @(
    "ingest-arxiv",
    "analyze-papers",
    "historical-backfill",
    "search-related"
)
if ($OperationsUsingTopicConfig -contains $Operation -and -not (Test-Path -LiteralPath $TopicConfig)) {
    throw "Topic configuration '$TopicConfig' does not exist."
}
if (-not [string]::IsNullOrWhiteSpace($LogicalDate) -and
    $Operation -notin @("ingest-arxiv", "analyze-papers")) {
    throw "LogicalDate is supported only by ingest-arxiv and analyze-papers."
}
if ($OperationArgument -contains "--topic-config") {
    throw "Use -TopicConfig instead of passing --topic-config through OperationArgument."
}
if ($OperationArgument -contains "--logical-date") {
    throw "Use -LogicalDate instead of passing --logical-date through OperationArgument."
}
if ($Operation -in @("historical-backfill", "search-related") -and
    [string]::IsNullOrWhiteSpace($env:SEMANTIC_SCHOLAR_API_KEY)) {
    throw "SEMANTIC_SCHOLAR_API_KEY is required for the '$Operation' operation."
}
if ($Operation -in @("analyze-papers", "search-related", "compare-papers") -and
    [string]::IsNullOrWhiteSpace($env:DEEPSEEK_API_KEY)) {
    throw "DEEPSEEK_API_KEY is required for the '$Operation' operation."
}

$Arguments = @(
    "run", "--frozen", "--python", "3.13.13",
    "paper-harness-daily", $Operation
)
if ($OperationsUsingTopicConfig -contains $Operation) {
    $Arguments += @("--topic-config", $TopicConfig)
}
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
$Arguments += $OperationArgument

& $Uv @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Daily operation '$Operation' failed with exit code $LASTEXITCODE."
}
