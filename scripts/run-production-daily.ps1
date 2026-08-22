[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    [string]$ProjectId,
    [string]$Region = "asia-southeast1",
    [string]$JobName = "paper-harness-daily",
    [string]$LogicalDate,
    [switch]$Reprocess
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Gcloud = (Get-Command gcloud -ErrorAction Stop).Source

$ActiveProject = (& $Gcloud config get-value project 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $ActiveProject -cne $ProjectId) {
    throw "The active gcloud project '$ActiveProject' does not match '$ProjectId'."
}

& $Gcloud run jobs describe $JobName --project=$ProjectId --region=$Region --format="value(name)"
if ($LASTEXITCODE -ne 0) {
    throw "Cloud Run Daily Job '$JobName' is unavailable."
}

$ExecutionArguments = @(
    "run", "jobs", "execute", $JobName,
    "--project=$ProjectId", "--region=$Region", "--tasks=1", "--wait"
)
$EnvironmentOverrides = @()
if ($LogicalDate) {
    $EnvironmentOverrides += "PIPELINE_LOGICAL_DATE=$LogicalDate"
}
if ($Reprocess) {
    $EnvironmentOverrides += "PIPELINE_REPROCESS=true"
}
if ($EnvironmentOverrides.Count -gt 0) {
    $ExecutionArguments += "--update-env-vars=$($EnvironmentOverrides -join ',')"
}

& $Gcloud @ExecutionArguments
if ($LASTEXITCODE -ne 0) {
    throw "Cloud Run Daily Job '$JobName' failed with exit code $LASTEXITCODE."
}
