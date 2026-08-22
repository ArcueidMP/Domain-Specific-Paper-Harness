[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    [string]$ProjectId,
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z0-9][a-z0-9._-]{0,127}$")]
    [string]$Tag,
    [string]$Region = "asia-southeast1",
    [string]$Repository = "paper-harness",
    [ValidateCount(1, 3)]
    [ValidateSet("web-api", "daily", "grobid")]
    [string[]]$Component = @("web-api", "daily", "grobid"),
    [switch]$Push,
    [switch]$PushExisting
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$Docker = (Get-Command docker -ErrorAction Stop).Source
$Registry = "$Region-docker.pkg.dev/$ProjectId/$Repository"

if ($Push -and $PushExisting) {
    throw "Use either -Push to build and push or -PushExisting to push without rebuilding."
}

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory)][string]$Command,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$FailureMessage
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage Exit code: $LASTEXITCODE."
    }
}

$Images = @(
    [pscustomobject]@{
        Name = "web-api"
        Arguments = @(
            "build", "--file", "infra/docker/Dockerfile.api",
            "--tag", "$Registry/web-api`:$Tag", "."
        )
    },
    [pscustomobject]@{
        Name = "daily"
        Arguments = @(
            "build", "--file", "infra/docker/Dockerfile.daily",
            "--target", "production",
            "--build-arg", "PREPARE_SPECTER2_BASE=1",
            "--tag", "$Registry/daily`:$Tag", "."
        )
    },
    [pscustomobject]@{
        Name = "grobid"
        Arguments = @(
            "build", "--file", "infra/docker/Dockerfile.grobid",
            "--tag", "$Registry/grobid`:$Tag", "."
        )
    }
)
$SelectedImages = @($Images | Where-Object { $Component -contains $_.Name })

Push-Location $RepositoryRoot
try {
    if (-not $PushExisting) {
        foreach ($Image in $SelectedImages) {
            Invoke-CheckedNative -Command $Docker -Arguments $Image.Arguments `
                -FailureMessage "Building $($Image.Name) failed."
        }
    }
    if ($Push -or $PushExisting) {
        foreach ($Image in $SelectedImages) {
            Invoke-CheckedNative -Command $Docker -Arguments @(
                "push", "$Registry/$($Image.Name)`:$Tag"
            ) -FailureMessage "Pushing $($Image.Name) failed."
        }
    }
}
finally {
    Pop-Location
}
