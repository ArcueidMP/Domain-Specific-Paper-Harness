[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")]
    [string]$BucketName,
    [Parameter(Mandatory)]
    [ValidatePattern("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    [string]$ProjectId,
    [string]$Location = "asia-southeast1",
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Gcloud = (Get-Command gcloud -ErrorAction Stop).Source
$ExpectedUri = "gs://$BucketName"
$ActiveProject = (& $Gcloud config get-value project 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $ActiveProject -ne $ProjectId) {
    throw "The active gcloud project '$ActiveProject' does not match '$ProjectId'."
}
$ExpectedProjectNumber = (& $Gcloud projects describe $ProjectId --format="value(projectNumber)" --quiet).Trim()
if ($LASTEXITCODE -ne 0 -or $ExpectedProjectNumber -notmatch "^[0-9]+$") {
    throw "The project number for '$ProjectId' could not be resolved."
}

function Get-JsonPropertyValue {
    param(
        [Parameter(Mandatory)]
        [object]$Object,
        [Parameter(Mandatory)]
        [string]$Name
    )

    $Property = $Object.PSObject.Properties[$Name]
    if ($null -eq $Property) {
        return $null
    }
    return $Property.Value
}

$PreviousPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    $BucketJson = (& $Gcloud storage buckets describe $ExpectedUri --format=json 2>$null) -join [Environment]::NewLine
    $DescribeExitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $PreviousPreference
}

if ($DescribeExitCode -eq 0) {
    $Bucket = $BucketJson | ConvertFrom-Json
    $OwnedBucketName = (& $Gcloud storage buckets list `
            --project=$ProjectId `
            --filter="name=$BucketName" `
            --format="value(name)" `
            --quiet).Trim()
    if ($LASTEXITCODE -ne 0 -or $OwnedBucketName -cne $BucketName) {
        throw "Existing state bucket '$BucketName' is not owned by project '$ProjectId'."
    }
    $ActualLocation = ([string](Get-JsonPropertyValue -Object $Bucket -Name "location")).ToLowerInvariant()
    if ($ActualLocation -ne $Location.ToLowerInvariant()) {
        throw "Existing state bucket '$BucketName' is in '$ActualLocation', not '$Location'."
    }

    $UniformAccess = Get-JsonPropertyValue -Object $Bucket -Name "uniform_bucket_level_access"
    $IamConfiguration = Get-JsonPropertyValue -Object $Bucket -Name "iamConfiguration"
    if ($null -eq $UniformAccess -and $null -ne $IamConfiguration) {
        $UniformConfiguration = Get-JsonPropertyValue `
            -Object $IamConfiguration `
            -Name "uniformBucketLevelAccess"
        if ($null -ne $UniformConfiguration) {
            $UniformAccess = Get-JsonPropertyValue -Object $UniformConfiguration -Name "enabled"
        }
    }
    if ($UniformAccess -ne $true) {
        throw "Existing state bucket '$BucketName' does not enforce uniform bucket-level access."
    }

    $PublicAccessPrevention = Get-JsonPropertyValue -Object $Bucket -Name "public_access_prevention"
    if ($null -eq $PublicAccessPrevention -and $null -ne $IamConfiguration) {
        $PublicAccessPrevention = Get-JsonPropertyValue `
            -Object $IamConfiguration `
            -Name "publicAccessPrevention"
    }
    if ([string]$PublicAccessPrevention -ne "enforced") {
        throw "Existing state bucket '$BucketName' does not enforce public access prevention."
    }

    $VersioningEnabled = Get-JsonPropertyValue -Object $Bucket -Name "versioning_enabled"
    if ($null -eq $VersioningEnabled) {
        $Versioning = Get-JsonPropertyValue -Object $Bucket -Name "versioning"
        if ($null -ne $Versioning) {
            $VersioningEnabled = Get-JsonPropertyValue -Object $Versioning -Name "enabled"
        }
    }
    if ($VersioningEnabled -ne $true) {
        throw "Existing state bucket '$BucketName' does not have object versioning enabled."
    }

    $SoftDeletePolicy = Get-JsonPropertyValue -Object $Bucket -Name "soft_delete_policy"
    if ($null -eq $SoftDeletePolicy) {
        $SoftDeletePolicy = Get-JsonPropertyValue -Object $Bucket -Name "softDeletePolicy"
    }
    $RetentionText = if ($null -eq $SoftDeletePolicy) {
        ""
    }
    else {
        [string](Get-JsonPropertyValue `
            -Object $SoftDeletePolicy `
            -Name "retentionDurationSeconds")
    }
    if ($RetentionText.EndsWith("s", [StringComparison]::Ordinal)) {
        $RetentionText = $RetentionText.Substring(0, $RetentionText.Length - 1)
    }
    [long]$RetentionSeconds = 0
    if (-not [long]::TryParse($RetentionText, [ref]$RetentionSeconds) -or
        $RetentionSeconds -lt 604800) {
        throw "Existing state bucket '$BucketName' does not use at least a seven-day soft-delete window."
    }
    Write-Host "Terraform state bucket '$BucketName' already satisfies the required controls."
    return
}

Write-Host "Planned one-time bootstrap: create '$ExpectedUri' in project '$ProjectId' and location '$Location'."
Write-Host "Controls: uniform bucket-level access, public access prevention, seven-day soft delete, and object versioning."
Write-Host "Cloud Storage is usage-billed; this creates no fixed-size compute resource."
if (-not $Apply) {
    Write-Host "No changes made. Re-run with -Apply to create the described bucket."
    return
}

& $Gcloud storage buckets create $ExpectedUri `
    --project=$ProjectId `
    --location=$Location `
    --uniform-bucket-level-access `
    --public-access-prevention `
    --soft-delete-duration=7d `
    --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Terraform state bucket creation failed."
}

& $Gcloud storage buckets update $ExpectedUri --versioning --quiet
if ($LASTEXITCODE -ne 0) {
    throw "The state bucket was created, but object versioning could not be enabled."
}

Write-Host "Terraform state bucket '$BucketName' was created with durable-state controls."
