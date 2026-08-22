[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    [string]$ProjectId,
    [Parameter(Mandatory)]
    [ValidatePattern("^[^@\s]+@[^@\s]+$")]
    [string]$OwnerEmail,
    [string]$Region = "asia-southeast1",
    [string]$NamePrefix = "paper-harness"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Gcloud = (Get-Command gcloud -ErrorAction Stop).Source

function Invoke-GcloudJson {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $Text = (& $Gcloud @Arguments --format=json) -join [Environment]::NewLine
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud command failed: $($Arguments -join ' ')."
    }
    return $Text | ConvertFrom-Json
}

function Test-ReadyCondition {
    param([Parameter(Mandatory)][object]$Resource)

    return @(
        $Resource.status.conditions |
            Where-Object { $_.type -eq "Ready" -and $_.status -eq "True" }
    ).Count -gt 0
}

function Assert-NoPublicPrincipal {
    param(
        [Parameter(Mandatory)][object]$Policy,
        [Parameter(Mandatory)][string]$ResourceName
    )

    $Members = @()
    if ($Policy.PSObject.Properties.Name -contains "bindings") {
        $Members = @($Policy.bindings | ForEach-Object { @($_.members) })
    }
    if ($Members -contains "allUsers" -or $Members -contains "allAuthenticatedUsers") {
        throw "Resource '$ResourceName' grants access to a public principal."
    }
}

$ActiveProject = (& $Gcloud config get-value project 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $ActiveProject -cne $ProjectId) {
    throw "The active gcloud project '$ActiveProject' does not match '$ProjectId'."
}

$WebName = "$NamePrefix-web"
$GrobidName = "$NamePrefix-grobid"
$DailyNames = @(
    "$NamePrefix-daily",
    "$NamePrefix-daily-brain-computer-interfaces",
    "$NamePrefix-daily-world-models"
)
$MigrationName = "$NamePrefix-migration"

foreach ($ServiceName in @($WebName, $GrobidName)) {
    $Service = Invoke-GcloudJson @(
        "run", "services", "describe", $ServiceName,
        "--project=$ProjectId", "--region=$Region"
    )
    if (-not (Test-ReadyCondition -Resource $Service)) {
        throw "Cloud Run service '$ServiceName' is not ready."
    }
    $Policy = Invoke-GcloudJson @(
        "run", "services", "get-iam-policy", $ServiceName,
        "--project=$ProjectId", "--region=$Region"
    )
    Assert-NoPublicPrincipal -Policy $Policy -ResourceName "Cloud Run service '$ServiceName'"
}

foreach ($JobName in @($DailyNames + $MigrationName)) {
    $Job = Invoke-GcloudJson @(
        "run", "jobs", "describe", $JobName,
        "--project=$ProjectId", "--region=$Region"
    )
    if (-not (Test-ReadyCondition -Resource $Job)) {
        throw "Cloud Run Job '$JobName' is not ready."
    }
    $Policy = Invoke-GcloudJson @(
        "run", "jobs", "get-iam-policy", $JobName,
        "--project=$ProjectId", "--region=$Region"
    )
    Assert-NoPublicPrincipal -Policy $Policy -ResourceName "Cloud Run Job '$JobName'"
}

$IapPolicy = Invoke-GcloudJson @(
    "iap", "web", "get-iam-policy",
    "--project=$ProjectId", "--resource-type=cloud-run",
    "--service=$WebName", "--region=$Region"
)
Assert-NoPublicPrincipal -Policy $IapPolicy -ResourceName "IAP policy for '$WebName'"
$IapMembers = @(
    $IapPolicy.bindings |
        Where-Object { $_.role -eq "roles/iap.httpsResourceAccessor" } |
        ForEach-Object { @($_.members) }
)
if ($IapMembers -cnotcontains "user:$OwnerEmail") {
    throw "The IAP allowlist does not contain the configured owner."
}

Write-Host "Private runtime configuration is ready and non-public."
