[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    [string]$ProjectId,
    [string]$Region = "asia-southeast1",
    [string]$SchedulerName = "paper-harness-daily",
    [ValidateSet("Describe", "Run")]
    [string]$Action = "Describe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Gcloud = (Get-Command gcloud -ErrorAction Stop).Source

$ActiveProject = (& $Gcloud config get-value project 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $ActiveProject -cne $ProjectId) {
    throw "The active gcloud project '$ActiveProject' does not match '$ProjectId'."
}

if ($Action -eq "Describe") {
    & $Gcloud scheduler jobs describe $SchedulerName --project=$ProjectId --location=$Region
}
elseif ($Action -eq "Run") {
    & $Gcloud scheduler jobs run $SchedulerName --project=$ProjectId --location=$Region
}
if ($LASTEXITCODE -ne 0) {
    throw "Scheduler action '$Action' failed with exit code $LASTEXITCODE."
}
