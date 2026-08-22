[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    [string]$ProjectId,
    [Parameter(Mandatory)]
    [ValidatePattern("^[^@\s]+@[^@\s]+$")]
    [string]$OwnerEmail,
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")]
    [string]$TerraformStateBucket,
    [string]$TerraformStatePrefix = "paper-harness/production",
    [Parameter(Mandatory)]
    [string]$VarFile,
    [ValidateSet("asia-southeast1")]
    [string]$Region = "asia-southeast1",
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$TerraformDirectory = Join-Path $RepositoryRoot "infra\terraform"
$Terraform = (Get-Command terraform -ErrorAction Stop).Source
$Gcloud = (Get-Command gcloud -ErrorAction Stop).Source
$ResolvedVarFile = (Resolve-Path -LiteralPath $VarFile -ErrorAction Stop).Path

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

$ActiveProject = (& $Gcloud config get-value project 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $ActiveProject -cne $ProjectId) {
    throw "The active gcloud project '$ActiveProject' does not match '$ProjectId'."
}

$CommonArguments = @(
    "-var-file=$ResolvedVarFile",
    "-var=project_id=$ProjectId",
    "-var=region=$Region",
    "-var=owner_email=$OwnerEmail"
)

Invoke-CheckedNative -Command $Terraform -Arguments @(
    "-chdir=$TerraformDirectory",
    "init",
    "-reconfigure",
    "-backend-config=bucket=$TerraformStateBucket",
    "-backend-config=prefix=$TerraformStatePrefix"
) -FailureMessage "Terraform initialization failed."

$Workspace = (& $Terraform "-chdir=$TerraformDirectory" workspace show).Trim()
if ($LASTEXITCODE -ne 0 -or $Workspace -cne "default") {
    throw "Production deployment requires the default Terraform workspace."
}

Invoke-CheckedNative -Command $Terraform -Arguments @(
    "-chdir=$TerraformDirectory", "fmt", "-check", "-recursive"
) -FailureMessage "Terraform formatting validation failed."
Invoke-CheckedNative -Command $Terraform -Arguments @(
    "-chdir=$TerraformDirectory", "validate"
) -FailureMessage "Terraform validation failed."

$Action = if ($Apply) { "apply" } else { "plan" }
Invoke-CheckedNative -Command $Terraform -Arguments @(
    "-chdir=$TerraformDirectory", $Action, $CommonArguments
) -FailureMessage "Terraform $Action failed."
